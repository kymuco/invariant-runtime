# invariant/ops/host.py
from __future__ import annotations

import concurrent.futures as cf
import logging
import os
import re
import threading
from collections.abc import Mapping
from typing import Any

from invariant.api.policies import EphemeralPolicy, ManagedPolicy
from invariant.api.policy_profiles import sandbox_limits_for_profile
from invariant.api.service import PluginService
from invariant.ops.errors import InvalidCommand, RuntimeErrorBase
from invariant.ops.supervisor import ExecutionSupervisor
from invariant.ops.watchdog import Watchdog

log = logging.getLogger(__name__)
_RUN_ID_MAX_LEN = 128
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class RuntimeHost:
    def __init__(
        self,
        *,
        plugin_service: PluginService,
        max_active_executions: int = 64,
        default_ttl_seconds: int = 300,
        cleanup_interval_seconds: int = 5,
        enable_watchdog: bool = True,
        plugin_call_workers: int = 16,
        plugin_call_queue_size: int = 128,
        default_plugin_call_timeout_s: float = 10.0,
        policy_profile: str | None = None,
    ):
        self._service = plugin_service
        self._supervisor = ExecutionSupervisor(
            plugin_service,
            max_active_executions=max_active_executions,
            default_ttl_seconds=default_ttl_seconds,
            cleanup_interval_seconds=cleanup_interval_seconds,
        )

        self._watchdog = Watchdog(
            interval_seconds=cleanup_interval_seconds,
            callback=self._safe_tick,
        )

        if enable_watchdog:
            self._watchdog.start()

        profile_raw = (
            str(policy_profile).strip()
            if isinstance(policy_profile, str) and str(policy_profile).strip()
            else str(os.environ.get("INVARIANT_POLICY_PROFILE", "")).strip()
        )
        self._policy_profile: str | None = profile_raw or None
        if profile_raw:
            limits = sandbox_limits_for_profile(profile_raw)
            policy_config = self._policy_config_for_profile(profile_raw)
            self._policies = {
                "ephemeral": EphemeralPolicy(limits=limits, config=policy_config),
                "managed": ManagedPolicy(limits=limits, config=policy_config),
            }
        else:
            self._policies = {
                "ephemeral": EphemeralPolicy(),
                "managed": ManagedPolicy(),
            }

        # ---- plugin call containment (bounded resources) ----
        w = int(plugin_call_workers)
        if w <= 0:
            raise ValueError("plugin_call_workers must be > 0")
        q = int(plugin_call_queue_size)
        if q <= 0:
            raise ValueError("plugin_call_queue_size must be > 0")
        # Keep at least as large as workers to avoid pathological starvation.
        q = max(q, w)

        self._default_plugin_call_timeout_s = float(default_plugin_call_timeout_s)
        if self._default_plugin_call_timeout_s <= 0:
            raise ValueError("default_plugin_call_timeout_s must be > 0")

        self._call_slots = threading.BoundedSemaphore(value=q)
        self._call_pool = cf.ThreadPoolExecutor(
            max_workers=w,
            thread_name_prefix="invariant-call",
        )

    def close(self) -> None:
        """Gracefully stop background maintenance."""
        try:
            self._watchdog.stop()
        except Exception:
            pass
        try:
            close_sup = getattr(self._supervisor, "close", None)
            if callable(close_sup):
                close_sup()
        except Exception:
            pass
        try:
            # We do not wait: hanging plugin calls must not block shutdown.
            self._call_pool.shutdown(wait=False, cancel_futures=True)  # py3.9+
        except TypeError:
            # older python: no cancel_futures
            try:
                self._call_pool.shutdown(wait=False)
            except Exception:
                pass
        except Exception:
            pass
        try:
            close_fn = getattr(self._service, "close", None)
            if callable(close_fn):
                close_fn()
        except Exception:
            pass

    def metrics(self):
        tracer = getattr(self._service, "tracer", None)
        return getattr(tracer, "metrics", None)

    def _safe_tick(self) -> None:
        # Runs in background thread; must be exception-free.
        try:
            self._supervisor.tick()
        except Exception:
            pass
        try:
            env_mgr = getattr(self._service, "env_manager", None)
            if env_mgr is not None:
                env_mgr.gc_envs()
        except Exception:
            pass

    def handle(self, command: dict[str, Any]) -> dict[str, Any]:
        # periodic maintenance (never raises)
        tick_cleaned = 0
        try:
            tick_cleaned = int(self._supervisor.tick() or 0)
        except Exception:
            pass
        
        try:
            cmd = command.get("cmd")
            if not cmd:
                raise InvalidCommand("Missing 'cmd'")

            if cmd == "create_execution":
                policy = self._parse_policy(command.get("policy"))
                overrides_raw = command.get("dependency_overrides") or {}
                overrides = self._parse_dependency_overrides(overrides_raw)
                workspace_id = command.get("workspace_id")
                workspace_options = command.get("workspace_options")
                workspace_path = command.get("workspace_path")
                if workspace_id is not None and not isinstance(workspace_id, str):
                    raise InvalidCommand("workspace_id must be a string")
                if workspace_options is not None and not isinstance(workspace_options, dict):
                    raise InvalidCommand("workspace_options must be an object")
                if workspace_path is not None and not isinstance(workspace_path, str):
                    raise InvalidCommand("workspace_path must be a string")
                ttl = command.get("ttl_seconds")
                if ttl is not None and not isinstance(ttl, int):
                    raise InvalidCommand("ttl_seconds must be an integer")
                execution_id = self._supervisor.create(
                    policy=policy,
                    dependency_overrides=overrides,
                    workspace_id=(workspace_id.strip() if isinstance(workspace_id, str) else None),
                    workspace_options=(dict(workspace_options) if isinstance(workspace_options, dict) else None),
                    workspace_path=(workspace_path.strip() if isinstance(workspace_path, str) else None),
                    ttl_seconds=ttl,
                )
                return {"ok": True, "execution": execution_id}

            if cmd == "execution_plugin_get":
                execution_id = command["execution"]
                plugin_id = command["plugin"]
                handle = self._supervisor.plugin_get(execution_id=execution_id, plugin_id=plugin_id)
                return {"ok": True, "handle": handle}

            if cmd == "execution_plugin_call":
                execution_id = command["execution"]
                handle = int(command["handle"])
                method = command["method"]
                args = command.get("args")
                kwargs = command.get("kwargs")
                timeout_s = command.get("timeout_s")
                if timeout_s is not None and not isinstance(timeout_s, (int, float)):
                    raise InvalidCommand("timeout_s must be a number")
                if args is not None and not isinstance(args, list):
                    raise InvalidCommand("args must be an array")
                if kwargs is not None and not isinstance(kwargs, dict):
                    raise InvalidCommand("kwargs must be an object")
                if timeout_s is None:
                    timeout = float(self._default_plugin_call_timeout_s)
                else:
                    if not isinstance(timeout_s, (int, float)):
                        raise InvalidCommand("timeout_s must be a number")
                    timeout = float(timeout_s)
                    if timeout <= 0:
                        raise InvalidCommand("timeout_s must be > 0")

                # bounded queue: prevents infinite thread growth / memory blow-ups
                acquired = False
                try:
                    acquired = self._call_slots.acquire(timeout=0.0)
                except Exception:
                    acquired = False
                if not acquired:
                    return {
                        "ok": False,
                        "error": "busy",
                        "message": "Plugin call queue is full. Try again.",
                    }

                try:
                    fut = self._call_pool.submit(
                        self._supervisor.plugin_call,
                        execution_id=execution_id,
                        handle=handle,
                        method=method,
                        args=args,
                        kwargs=kwargs,
                        timeout_s=float(timeout_s) if timeout_s is not None else None,
                    )
                except Exception:
                    # submission failed => release slot immediately
                    try:
                        self._call_slots.release()
                    except Exception:
                        pass
                    raise

                def _release(_f) -> None:
                    try:
                        self._call_slots.release()
                    except Exception:
                        pass

                fut.add_done_callback(_release)

                try:
                    result = fut.result(timeout=timeout)
                    return {"ok": True, "result": result}
                except cf.TimeoutError:
                    # best-effort cancel (won't stop a running thread, but may cancel if not started)
                    try:
                        fut.cancel()
                    except Exception:
                        pass
                    # containment: execution must be closed on timeout
                    try:
                        self._supervisor.rollback(execution_id)
                    except Exception:
                        pass
                    return {
                        "ok": False,
                        "error": "timeout",
                        "message": f"plugin_call exceeded timeout_s={timeout}",
                    }

            if cmd == "execution_commit":
                self._supervisor.commit(command["execution"])
                return {"ok": True}

            if cmd == "execution_rollback":
                self._supervisor.rollback(command["execution"])
                return {"ok": True}
            
            if cmd == "execution_touch":
                execution_id = command["execution"]
                ttl = command.get("ttl_seconds")
                if ttl is not None and not isinstance(ttl, int):
                    raise InvalidCommand("ttl_seconds must be an integer")
                info = self._supervisor.touch(execution_id, ttl_seconds=ttl)
                return {"ok": True, **info}

            # ---- app-level Agent Factory run API ----
            if cmd == "run_submit":
                pipeline = command.get("pipeline")
                if not isinstance(pipeline, str) or not pipeline.strip():
                    raise InvalidCommand("pipeline must be a non-empty string")
                inputs = command.get("inputs")
                if inputs is None:
                    inputs_obj: dict[str, Any] = {}
                elif isinstance(inputs, dict):
                    inputs_obj = dict(inputs)
                else:
                    raise InvalidCommand("inputs must be an object")

                policy = self._parse_policy(command.get("policy"))
                ttl = command.get("ttl_seconds", 1200)
                if not isinstance(ttl, int):
                    raise InvalidCommand("ttl_seconds must be an integer")

                fail_fast = command.get("fail_fast")
                if fail_fast is not None and not isinstance(fail_fast, bool):
                    raise InvalidCommand("fail_fast must be boolean|null")
                include_outputs = command.get("include_outputs")
                if include_outputs is not None and not isinstance(include_outputs, bool):
                    raise InvalidCommand("include_outputs must be boolean|null")
                max_steps = command.get("max_steps")
                if max_steps is not None and not isinstance(max_steps, int):
                    raise InvalidCommand("max_steps must be integer|null")

                workspace_id = command.get("workspace_id")
                workspace_options = command.get("workspace_options")
                workspace_path = command.get("workspace_path")
                if workspace_id is not None and not isinstance(workspace_id, str):
                    raise InvalidCommand("workspace_id must be a string")
                if workspace_options is not None and not isinstance(workspace_options, dict):
                    raise InvalidCommand("workspace_options must be an object")
                if workspace_path is not None and not isinstance(workspace_path, str):
                    raise InvalidCommand("workspace_path must be a string")
                if workspace_id and workspace_path:
                    raise InvalidCommand("workspace_id and workspace_path are mutually exclusive")

                idempotency_key = command.get("idempotency_key")
                if idempotency_key is not None and not isinstance(idempotency_key, str):
                    raise InvalidCommand("idempotency_key must be a string")
                defer_execution = command.get("defer_execution", False)
                if not isinstance(defer_execution, bool):
                    raise InvalidCommand("defer_execution must be a boolean")
                max_attempts = command.get("max_attempts", 3)
                if not isinstance(max_attempts, int):
                    raise InvalidCommand("max_attempts must be an integer")
                lease_seconds = command.get("lease_seconds", 120)
                if not isinstance(lease_seconds, int):
                    raise InvalidCommand("lease_seconds must be an integer")
                step_timeout_seconds = command.get("step_timeout_seconds", 1200)
                if not isinstance(step_timeout_seconds, int):
                    raise InvalidCommand("step_timeout_seconds must be an integer")
                backoff_base_seconds = command.get("backoff_base_seconds", 5)
                if not isinstance(backoff_base_seconds, int):
                    raise InvalidCommand("backoff_base_seconds must be an integer")
                experimental = command.get("experimental", False)
                if not isinstance(experimental, bool):
                    raise InvalidCommand("experimental must be a boolean")

                run = self._agent_run_call(
                    "run_submit",
                    pipeline=str(pipeline).strip(),
                    inputs=inputs_obj,
                    policy=policy,
                    policy_profile=self._policy_profile,
                    ttl_seconds=int(ttl),
                    idempotency_key=(str(idempotency_key).strip() if isinstance(idempotency_key, str) else None),
                    fail_fast=fail_fast,
                    include_outputs=include_outputs,
                    max_steps=max_steps,
                    workspace_id=(workspace_id.strip() if isinstance(workspace_id, str) else None),
                    workspace_options=(dict(workspace_options) if isinstance(workspace_options, dict) else None),
                    workspace_path=(workspace_path.strip() if isinstance(workspace_path, str) else None),
                    allow_experimental=bool(experimental),
                    defer_execution=bool(defer_execution),
                    max_attempts=int(max_attempts),
                    lease_seconds=int(lease_seconds),
                    step_timeout_seconds=int(step_timeout_seconds),
                    backoff_base_seconds=int(backoff_base_seconds),
                )
                return {"ok": True, "run": run}

            if cmd == "agent_run_status":
                run_id = self._normalize_run_id(command.get("run_id"))
                out = self._agent_run_call("run_status", run_id=run_id)
                return {"ok": True, "run": out}

            if cmd == "agent_run_report":
                run_id = self._normalize_run_id(command.get("run_id"))
                out = self._agent_run_call("run_report", run_id=run_id)
                return {"ok": True, "report": out}

            if cmd == "agent_run_list":
                limit = command.get("limit", 50)
                if not isinstance(limit, int):
                    raise InvalidCommand("limit must be an integer")
                out = self._agent_run_call("run_list", limit=int(limit))
                return {"ok": True, **out}

            if cmd == "agent_run_health":
                limit = command.get("limit", 1000)
                if not isinstance(limit, int):
                    raise InvalidCommand("limit must be an integer")
                repair = command.get("repair", False)
                if not isinstance(repair, bool):
                    raise InvalidCommand("repair must be a boolean")
                out = self._agent_run_call("run_health", limit=int(limit), repair=bool(repair))
                return {"ok": True, "health": out}

            if cmd == "agent_run_observability":
                limit = command.get("limit", 1000)
                if not isinstance(limit, int):
                    raise InvalidCommand("limit must be an integer")
                fail_spike_window = command.get("fail_spike_window", 20)
                if not isinstance(fail_spike_window, int):
                    raise InvalidCommand("fail_spike_window must be an integer")
                fail_spike_threshold_pct = command.get("fail_spike_threshold_pct", 40.0)
                if not isinstance(fail_spike_threshold_pct, (int, float)):
                    raise InvalidCommand("fail_spike_threshold_pct must be numeric")
                stuck_run_seconds = command.get("stuck_run_seconds", 1800)
                if not isinstance(stuck_run_seconds, int):
                    raise InvalidCommand("stuck_run_seconds must be an integer")
                replay_target_pct = command.get("replay_target_pct", 95.0)
                if not isinstance(replay_target_pct, (int, float)):
                    raise InvalidCommand("replay_target_pct must be numeric")
                trace_target_pct = command.get("trace_target_pct", 100.0)
                if not isinstance(trace_target_pct, (int, float)):
                    raise InvalidCommand("trace_target_pct must be numeric")

                out = self._agent_run_call(
                    "run_observability",
                    limit=int(limit),
                    fail_spike_window=int(fail_spike_window),
                    fail_spike_threshold_pct=float(fail_spike_threshold_pct),
                    stuck_run_seconds=int(stuck_run_seconds),
                    replay_target_pct=float(replay_target_pct),
                    trace_target_pct=float(trace_target_pct),
                )
                return {"ok": True, "observability": out}

            if cmd == "agent_run_queue_stats":
                out = self._agent_run_call("run_queue_stats")
                return {"ok": True, "queue": out}

            if cmd == "agent_run_queue_reclaim":
                limit = command.get("limit", 100)
                if not isinstance(limit, int):
                    raise InvalidCommand("limit must be an integer")
                out = self._agent_run_call("run_queue_reclaim", limit=int(limit))
                return {"ok": True, **out}

            if cmd == "agent_run_worker_process":
                worker_id = command.get("worker_id")
                if worker_id is None:
                    worker_id = ""
                if not isinstance(worker_id, str):
                    raise InvalidCommand("worker_id must be a string")
                lease_seconds = command.get("lease_seconds")
                if lease_seconds is not None and not isinstance(lease_seconds, int):
                    raise InvalidCommand("lease_seconds must be integer|null")
                reclaim_expired = command.get("reclaim_expired", True)
                if not isinstance(reclaim_expired, bool):
                    raise InvalidCommand("reclaim_expired must be a boolean")
                out = self._agent_run_call(
                    "run_queue_process",
                    worker_id=str(worker_id).strip(),
                    lease_seconds=(int(lease_seconds) if isinstance(lease_seconds, int) else None),
                    reclaim_expired=bool(reclaim_expired),
                )
                return {"ok": True, "worker": out}

            if cmd == "agent_run_dlq":
                limit = command.get("limit", 100)
                if not isinstance(limit, int):
                    raise InvalidCommand("limit must be an integer")
                out = self._agent_run_call("run_dlq", limit=int(limit))
                return {"ok": True, "dlq": out}

            if cmd == "agent_run_migrate_storage":
                dry_run = command.get("dry_run", True)
                if not isinstance(dry_run, bool):
                    raise InvalidCommand("dry_run must be a boolean")
                backup_legacy = command.get("backup_legacy", True)
                if not isinstance(backup_legacy, bool):
                    raise InvalidCommand("backup_legacy must be a boolean")
                prune_legacy = command.get("prune_legacy", False)
                if not isinstance(prune_legacy, bool):
                    raise InvalidCommand("prune_legacy must be a boolean")
                force = command.get("force", False)
                if not isinstance(force, bool):
                    raise InvalidCommand("force must be a boolean")
                out = self._agent_run_call(
                    "run_migrate_storage",
                    dry_run=bool(dry_run),
                    backup_legacy=bool(backup_legacy),
                    prune_legacy=bool(prune_legacy),
                    force=bool(force),
                )
                return {"ok": True, "migration": out}

            if cmd == "agent_run_cancel":
                run_id = self._normalize_run_id(command.get("run_id"))
                reason = command.get("reason", "cancel_requested")
                if not isinstance(reason, str):
                    raise InvalidCommand("reason must be a string")
                out = self._agent_run_call("run_cancel", run_id=run_id, reason=str(reason).strip())
                return {"ok": True, "run": out}

            if cmd == "agent_run_resume":
                run_id = self._normalize_run_id(command.get("run_id"))
                policy = self._parse_policy(command.get("policy"))
                ttl = command.get("ttl_seconds", 1200)
                if not isinstance(ttl, int):
                    raise InvalidCommand("ttl_seconds must be an integer")
                workspace_id = command.get("workspace_id")
                workspace_options = command.get("workspace_options")
                workspace_path = command.get("workspace_path")
                if workspace_id is not None and not isinstance(workspace_id, str):
                    raise InvalidCommand("workspace_id must be a string")
                if workspace_options is not None and not isinstance(workspace_options, dict):
                    raise InvalidCommand("workspace_options must be an object")
                if workspace_path is not None and not isinstance(workspace_path, str):
                    raise InvalidCommand("workspace_path must be a string")
                if workspace_id and workspace_path:
                    raise InvalidCommand("workspace_id and workspace_path are mutually exclusive")
                out = self._agent_run_call(
                    "run_resume",
                    run_id=run_id,
                    policy=policy,
                    ttl_seconds=int(ttl),
                    workspace_id=(workspace_id.strip() if isinstance(workspace_id, str) else None),
                    workspace_options=(dict(workspace_options) if isinstance(workspace_options, dict) else None),
                    workspace_path=(workspace_path.strip() if isinstance(workspace_path, str) else None),
                )
                return {"ok": True, "run": out}

            if cmd == "agent_run_abort":
                run_id = self._normalize_run_id(command.get("run_id"))
                policy = self._parse_policy(command.get("policy"))
                ttl = command.get("ttl_seconds", 1200)
                if not isinstance(ttl, int):
                    raise InvalidCommand("ttl_seconds must be an integer")
                workspace_id = command.get("workspace_id")
                workspace_options = command.get("workspace_options")
                workspace_path = command.get("workspace_path")
                if workspace_id is not None and not isinstance(workspace_id, str):
                    raise InvalidCommand("workspace_id must be a string")
                if workspace_options is not None and not isinstance(workspace_options, dict):
                    raise InvalidCommand("workspace_options must be an object")
                if workspace_path is not None and not isinstance(workspace_path, str):
                    raise InvalidCommand("workspace_path must be a string")
                if workspace_id and workspace_path:
                    raise InvalidCommand("workspace_id and workspace_path are mutually exclusive")
                out = self._agent_run_call(
                    "run_abort",
                    run_id=run_id,
                    policy=policy,
                    ttl_seconds=int(ttl),
                    workspace_id=(workspace_id.strip() if isinstance(workspace_id, str) else None),
                    workspace_options=(dict(workspace_options) if isinstance(workspace_options, dict) else None),
                    workspace_path=(workspace_path.strip() if isinstance(workspace_path, str) else None),
                )
                return {"ok": True, "run": out}

            if cmd == "agent_run_approve":
                run_id = self._normalize_run_id(command.get("run_id"))
                stage = command.get("stage")
                if not isinstance(stage, str) or not stage.strip():
                    raise InvalidCommand("stage must be a non-empty string")
                token = command.get("token")
                if not isinstance(token, str) or not token.strip():
                    raise InvalidCommand("token must be a non-empty string")
                policy = self._parse_policy(command.get("policy"))
                ttl = command.get("ttl_seconds", 1200)
                if not isinstance(ttl, int):
                    raise InvalidCommand("ttl_seconds must be an integer")
                workspace_id = command.get("workspace_id")
                workspace_options = command.get("workspace_options")
                workspace_path = command.get("workspace_path")
                if workspace_id is not None and not isinstance(workspace_id, str):
                    raise InvalidCommand("workspace_id must be a string")
                if workspace_options is not None and not isinstance(workspace_options, dict):
                    raise InvalidCommand("workspace_options must be an object")
                if workspace_path is not None and not isinstance(workspace_path, str):
                    raise InvalidCommand("workspace_path must be a string")
                if workspace_id and workspace_path:
                    raise InvalidCommand("workspace_id and workspace_path are mutually exclusive")
                out = self._agent_run_call(
                    "run_approve",
                    run_id=run_id,
                    stage=str(stage).strip(),
                    token=str(token).strip(),
                    policy=policy,
                    ttl_seconds=int(ttl),
                    workspace_id=(workspace_id.strip() if isinstance(workspace_id, str) else None),
                    workspace_options=(dict(workspace_options) if isinstance(workspace_options, dict) else None),
                    workspace_path=(workspace_path.strip() if isinstance(workspace_path, str) else None),
                )
                return {"ok": True, "run": out}

            if cmd == "agent_run_replay":
                run_id = self._normalize_run_id(command.get("run_id"))
                policy = self._parse_policy(command.get("policy"))
                ttl = command.get("ttl_seconds", 1200)
                if not isinstance(ttl, int):
                    raise InvalidCommand("ttl_seconds must be an integer")
                strict = command.get("strict", True)
                if not isinstance(strict, bool):
                    raise InvalidCommand("strict must be a boolean")
                workspace_id = command.get("workspace_id")
                workspace_options = command.get("workspace_options")
                workspace_path = command.get("workspace_path")
                if workspace_id is not None and not isinstance(workspace_id, str):
                    raise InvalidCommand("workspace_id must be a string")
                if workspace_options is not None and not isinstance(workspace_options, dict):
                    raise InvalidCommand("workspace_options must be an object")
                if workspace_path is not None and not isinstance(workspace_path, str):
                    raise InvalidCommand("workspace_path must be a string")
                if workspace_id and workspace_path:
                    raise InvalidCommand("workspace_id and workspace_path are mutually exclusive")
                out = self._agent_run_call(
                    "run_replay",
                    run_id=run_id,
                    policy=policy,
                    ttl_seconds=int(ttl),
                    workspace_id=(workspace_id.strip() if isinstance(workspace_id, str) else None),
                    workspace_options=(dict(workspace_options) if isinstance(workspace_options, dict) else None),
                    workspace_path=(workspace_path.strip() if isinstance(workspace_path, str) else None),
                    strict=bool(strict),
                )
                return {"ok": True, "replay": out}

            if cmd == "agent_run_replay_batch":
                limit = command.get("limit", 200)
                if not isinstance(limit, int):
                    raise InvalidCommand("limit must be an integer")
                max_runs = command.get("max_runs", 20)
                if not isinstance(max_runs, int):
                    raise InvalidCommand("max_runs must be an integer")
                cooldown_seconds = command.get("cooldown_seconds", 900.0)
                if not isinstance(cooldown_seconds, (int, float)):
                    raise InvalidCommand("cooldown_seconds must be numeric")
                strict = command.get("strict", True)
                if not isinstance(strict, bool):
                    raise InvalidCommand("strict must be a boolean")
                policy = self._parse_policy(command.get("policy"))
                ttl = command.get("ttl_seconds", 1200)
                if not isinstance(ttl, int):
                    raise InvalidCommand("ttl_seconds must be an integer")
                workspace_id = command.get("workspace_id")
                workspace_options = command.get("workspace_options")
                workspace_path = command.get("workspace_path")
                if workspace_id is not None and not isinstance(workspace_id, str):
                    raise InvalidCommand("workspace_id must be a string")
                if workspace_options is not None and not isinstance(workspace_options, dict):
                    raise InvalidCommand("workspace_options must be an object")
                if workspace_path is not None and not isinstance(workspace_path, str):
                    raise InvalidCommand("workspace_path must be a string")
                if workspace_id and workspace_path:
                    raise InvalidCommand("workspace_id and workspace_path are mutually exclusive")
                out = self._agent_run_call(
                    "run_replay_batch",
                    limit=int(limit),
                    max_runs=int(max_runs),
                    cooldown_seconds=float(cooldown_seconds),
                    strict=bool(strict),
                    policy=policy,
                    ttl_seconds=int(ttl),
                    workspace_id=(workspace_id.strip() if isinstance(workspace_id, str) else None),
                    workspace_options=(dict(workspace_options) if isinstance(workspace_options, dict) else None),
                    workspace_path=(workspace_path.strip() if isinstance(workspace_path, str) else None),
                )
                return {"ok": True, "batch": out}
            
            # ---- maintenance ----
            if cmd == "list_executions":
                return {"ok": True, "executions": self._supervisor.list_executions()}
            
            if cmd == "list_plugins":
                items = []
                for d in self._service.registry.list():
                    items.append({
                        "id": d.meta.id,
                        "version": d.meta.version,
                        "name": getattr(d.meta, "name", None),
                        "deps": [dep.id for dep in (d.meta.dependencies or [])],
                    })
                return {"ok": True, "plugins": items}

            if cmd == "cleanup_expired":
                n = int(self._supervisor.cleanup_expired() or 0)
                return {"ok": True, "cleaned": tick_cleaned + n}

            # ---- tracing ----
            if cmd == "trace_execution":
                execution_id = command["execution"]
                tracer = getattr(self._service, "tracer", None)
                events = tracer.execution_trace(execution_id) if tracer else []
                return {"ok": True, "events": events}

            if cmd == "trace_execution_for_tx":
                tx_id = command["tx_id"]
                tracer = getattr(self._service, "tracer", None)
                execution = tracer.execution_for_tx(tx_id) if tracer else None
                return {"ok": True, "execution": execution}

            if cmd == "trace_recent_activations":
                tracer = getattr(self._service, "tracer", None)
                tx_ids = tracer.recent_activation_txs() if tracer else []
                return {"ok": True, "tx_ids": tx_ids}

            if cmd == "trace_activation":
                tx_id = command["tx_id"]
                tracer = getattr(self._service, "tracer", None)
                events = tracer.activation_trace(tx_id) if tracer else []
                return {"ok": True, "events": events}

            if cmd == "trace_plugin":
                plugin_id = command["plugin"]
                tracer = getattr(self._service, "tracer", None)
                events = tracer.plugin_lifecycle(plugin_id) if tracer else []
                return {"ok": True, "events": events}
            
            if cmd == "metrics_snapshot":
                tracer = getattr(self._service, "tracer", None)
                metrics = getattr(tracer, "metrics", None)
                return {"ok": True, "metrics": metrics.snapshot() if metrics else {}}

            raise InvalidCommand(f"Unknown command: {cmd}")

        except RuntimeErrorBase as e:
            payload = getattr(e, "payload", None)
            if callable(payload):
                p = payload()
                out = {"ok": False, "error": p.code, "message": p.message}
                if p.detail is not None:
                    out["detail"] = p.detail
                if p.extra is not None:
                    out["extra"] = p.extra
                return out
            return {"ok": False, "error": getattr(e, "code", e.__class__.__name__), "message": str(e)}
        except Exception as e:
            return {"ok": False, "error": "internal_error", "message": str(e)}

    def _agent_run_call(self, method_name: str, /, **kwargs: Any) -> dict[str, Any]:
        method = getattr(self._service, method_name, None)
        if not callable(method):
            raise InvalidCommand("agent run API is not available in this service")
        result = method(**kwargs)
        if not isinstance(result, dict):
            raise InvalidCommand(f"{method_name} must return object payload")
        return dict(result)

    @staticmethod
    def _normalize_run_id(raw: Any) -> str:
        if not isinstance(raw, str):
            raise InvalidCommand("run_id must be a non-empty string")
        run_id = raw.strip()
        if not run_id:
            raise InvalidCommand("run_id must be a non-empty string")
        if len(run_id) > _RUN_ID_MAX_LEN:
            raise InvalidCommand(f"run_id must be <= {_RUN_ID_MAX_LEN} chars")
        if ".." in run_id:
            raise InvalidCommand("run_id has invalid format")
        if _RUN_ID_PATTERN.fullmatch(run_id) is None:
            raise InvalidCommand("run_id has invalid format")
        return run_id

    def _policy_config_for_profile(self, profile: str) -> dict[str, Any]:
        provider = getattr(self._service, "policy_config_for_profile", None)
        if not callable(provider):
            return {}
        raw = provider(profile)
        if raw is None:
            return {}
        if not isinstance(raw, Mapping):
            raise InvalidCommand("policy_config_for_profile must return object-like mapping")
        cfg = dict(raw)
        log.info("Loaded profile config keys=%s profile=%s", len(cfg), profile)
        return cfg

    def _parse_policy(self, name: object | None):
        if name is None or name == "ephemeral":
            return self._policies["ephemeral"]
        if name == "managed":
            return self._policies["managed"]
        raise InvalidCommand(f"Unknown policy: {name}")

    def _parse_dependency_overrides(self, raw: Any) -> dict[str, object]:
            """
            JSON-safe overrides:
            {"some.plugin": "managed", "other.plugin": "ephemeral"}
            Values are policy names, not arbitrary objects (prevents DoS / policy injection).
            """
            if raw is None:
                return {}
            if not isinstance(raw, dict):
                raise InvalidCommand("dependency_overrides must be an object")
            out: dict[str, object] = {}
            for k, v in raw.items():
                if not isinstance(k, str) or not k.strip():
                    raise InvalidCommand("dependency_overrides keys must be non-empty strings")
                if v is None:
                    continue
                if not isinstance(v, str):
                    raise InvalidCommand("dependency_overrides values must be policy names (string)")
                out[k] = self._parse_policy(v.strip())
            return out
