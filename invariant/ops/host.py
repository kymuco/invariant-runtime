# invariant/ops/host.py
from __future__ import annotations

import concurrent.futures as cf
import threading
from typing import Any, Dict

from invariant.api.service import PluginService
from invariant.api.policies import ManagedPolicy, EphemeralPolicy

from invariant.ops.supervisor import ExecutionSupervisor
from invariant.ops.errors import RuntimeErrorBase, InvalidCommand
from invariant.ops.watchdog import Watchdog


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

    def handle(self, command: Dict[str, Any]) -> Dict[str, Any]:
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
                ttl = command.get("ttl_seconds")
                if ttl is not None and not isinstance(ttl, int):
                    raise InvalidCommand("ttl_seconds must be an integer")
                execution_id = self._supervisor.create(
                    policy=policy,
                    dependency_overrides=overrides,
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

    def _parse_policy(self, name: str):
        if name in (None, "ephemeral"):
            return self._policies["ephemeral"]
        if name == "managed":
            return self._policies["managed"]
        raise InvalidCommand(f"Unknown policy: {name}")

    def _parse_dependency_overrides(self, raw: Any) -> Dict[str, object]:
            """
            JSON-safe overrides:
            {"some.plugin": "managed", "other.plugin": "ephemeral"}
            Values are policy names, not arbitrary objects (prevents DoS / policy injection).
            """
            if raw is None:
                return {}
            if not isinstance(raw, dict):
                raise InvalidCommand("dependency_overrides must be an object")
            out: Dict[str, object] = {}
            for k, v in raw.items():
                if not isinstance(k, str) or not k.strip():
                    raise InvalidCommand("dependency_overrides keys must be non-empty strings")
                if v is None:
                    continue
                if not isinstance(v, str):
                    raise InvalidCommand("dependency_overrides values must be policy names (string)")
                out[k] = self._parse_policy(v.strip())
            return out
