# invariant/exec/runtime/execution_context.py
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from invariant.api.policies import EphemeralPolicy, ManagedPolicy
from invariant.api.types import WorkspaceOptions
from invariant.exec.caps.fs import FSAccess
from invariant.exec.caps.registry import CapabilityProvider, is_capability_ref, split_id_version
from invariant.exec.runtime.execution_runtime import ExecutionRuntime
from invariant.exec.runtime.resolver import DependencyResolver
from invariant.ops.fs import rmtree_best_effort
from invariant.spec.dependency import DependencyScope
from invariant.spec.errors import DependencyCycleError

logger = logging.getLogger(__name__)


class ExecutionContext:
    """
    Execution-scoped plugin context.
    Supports per-dependency policy overrides.
    """

    def __init__(
        self,
        *,
        plugin_service,
        policy,
        dependency_overrides: dict[str, object] | None = None,
        workspace_id: str | None = None,
        workspace_options=None,
        workspace_path: str | Path | None = None,
        cleanup_sandbox: bool = True,
    ):
        self.id = str(uuid.uuid4())
        self._closed = False

        self._plugin_service = plugin_service
        self._root_policy = policy
        self._dependency_overrides = dependency_overrides or {}

        # runtime per policy (lazy)
        self._runtimes: dict[tuple[Any, ...], ExecutionRuntime] = {}

        # cross-runtime cycle guard
        self._activation_stack: list[tuple[tuple[Any, ...], str]] = []

        # reuse policies (do NOT create new instances per dependency)
        self._managed_policy = ManagedPolicy()
        self._ephemeral_policy = EphemeralPolicy()

        # ---- execution-scoped capabilities (base provider) ----
        self._capabilities_base = CapabilityProvider(strict_versions=True)
        # Public view is assigned later after root runtime is created:
        #   self.capabilities = self._root_runtime.capabilities
        # self.capabilities = None

        from invariant.exec.caps.config_source import EnvDictConfigSource
        from invariant.exec.caps.ids import (
            CAP_CONFIG_SOURCE,
            CAP_FS_SANDBOX,
            CAP_HTTP,
            CAP_INSPECTOR,
            CAP_KV,
            CAP_PROCESS,
            CAP_WORKSPACE,
        )

        sandbox_root = Path.cwd() / ".invariant" / "sandbox" / self.id
        self._sandbox_root = sandbox_root
        self._cleanup_sandbox = bool(cleanup_sandbox)

        limits = getattr(policy, "limits", None)
        config = getattr(policy, "config", None) or {}

        allow_fs_write = bool(getattr(limits, "allow_fs_write", False))
        allow_fs_delete = bool(getattr(limits, "allow_fs_delete", False))
        max_read_bytes = int(getattr(limits, "max_read_bytes", 2_000_000))
        max_write_bytes = int(getattr(limits, "max_write_bytes", 2_000_000))

        self._capabilities_base.register(
            CAP_FS_SANDBOX,
            FSAccess(
                root=sandbox_root,
                write=allow_fs_write,
                delete=allow_fs_delete,
                read_limit=max_read_bytes,
                write_limit=max_write_bytes,
            ),
        )

        self._capabilities_base.register(
            CAP_CONFIG_SOURCE,
            EnvDictConfigSource(
                config,
                allow_env=bool(getattr(limits, "allow_env_config", False)),
                env_prefix=str(getattr(limits, "config_env_prefix", "INVARIANT_") or "INVARIANT_"),
            ),
        )

        if workspace_id and workspace_path is not None:
            raise ValueError("workspace_id and workspace_path are mutually exclusive")

        if workspace_id or workspace_path is not None:
            opts = workspace_options or WorkspaceOptions()
            if workspace_path is not None:
                ws_path = Path(str(workspace_path)).expanduser()
                workspace_root = ws_path if ws_path.is_absolute() else (Path.cwd() / ws_path)
            else:
                # IMPORTANT: workspace root is controlled by policy limits, not user options.
                ws_root_dir = getattr(limits, "workspace_root_dirname", None)
                if not ws_root_dir:
                    # backward compat fallback (still validated in WorkspaceOptions)
                    ws_root_dir = opts.root_dirname
                workspace_root = Path.cwd() / str(ws_root_dir) / str(workspace_id)

            # Merge permissions: options can only reduce rights, never increase them.
            pol_write = bool(getattr(limits, "allow_fs_write", False))
            pol_delete = bool(getattr(limits, "allow_fs_delete", False))
            eff_write = bool(opts.allow_write) and pol_write
            eff_delete = bool(opts.allow_delete) and pol_delete

            pol_r = int(getattr(limits, "workspace_max_read_bytes", getattr(limits, "max_read_bytes", 2_000_000)))
            pol_w = int(getattr(limits, "workspace_max_write_bytes", getattr(limits, "max_write_bytes", 2_000_000)))
            eff_r = min(int(opts.max_read_bytes), pol_r)
            eff_w = min(int(opts.max_write_bytes), pol_w)

            workspace_fs = FSAccess(
                root=workspace_root,
                write=eff_write,
                delete=eff_delete,
                read_limit=eff_r,
                write_limit=eff_w,
            )
            self._capabilities_base.register(CAP_WORKSPACE, workspace_fs)

            if bool(getattr(limits, "allow_process", False)):
                from invariant.exec.caps.process import ProcessAccess

                self._capabilities_base.register(
                    CAP_PROCESS,
                    ProcessAccess(
                        workspace=workspace_fs,
                        max_timeout_s=float(getattr(limits, "process_max_timeout_s", 120.0)),
                        max_output_chars=int(getattr(limits, "process_max_output_chars", 40_000)),
                    ),
                )

        # ---- optional: KV (store state for pipelines) ----
        try:
            allow_kv = bool(getattr(limits, "allow_kv", True))
        except Exception:
            allow_kv = True

        if allow_kv:
            from invariant.exec.caps.kv import KVStore
            kv_fs = None
            if workspace_id:
                kv_fs = self._capabilities_base.get(CAP_WORKSPACE)
            else:
                kv_fs = self._capabilities_base.get(CAP_FS_SANDBOX)

            # KV requires write-capable FS. If FS writes are disabled, do not register KV.
            can_write = bool(getattr(kv_fs, "allow_write", lambda: False)())
            if can_write:
                self._capabilities_base.register(
                    CAP_KV,
                    KVStore(
                        fs=kv_fs,
                        rel_path=".invariant/kv.json",
                        max_bytes=int(getattr(limits, "kv_max_bytes", 1_000_000)),
                    ),
                )

        # ---- optional: HTTP (deny-all by default; requires allow_network) ----
        if bool(getattr(limits, "allow_network", False)):
            from invariant.exec.caps.http import HttpAccess
            self._capabilities_base.register(
                CAP_HTTP,
                HttpAccess(
                    allow_hosts=tuple(getattr(limits, "http_allow_hosts", ()) or ()),
                    timeout_s=float(getattr(limits, "http_timeout_s", 10.0)),
                    max_bytes=int(getattr(limits, "http_max_bytes", 2_000_000)),
                    allow_any_host=bool(getattr(limits, "http_allow_any_host", False)),
                    allow_redirects=bool(getattr(limits, "http_allow_redirects", False)),
                    allow_private_ips=bool(getattr(limits, "http_allow_private_ips", False)),
                ),
            )

        # root runtime
        self._root_runtime = self._get_runtime(policy)

        # Expose ONLY policy-gated view to the outside world.
        self.capabilities = self._root_runtime.capabilities

        from invariant.exec.caps.inspector import RuntimeInspector

        self._capabilities_base.register(
            CAP_INSPECTOR,
            RuntimeInspector(
                registry=self._plugin_service.registry,
                runtime=self._root_runtime,
                capabilities=self._root_runtime.capabilities,  # policy-gated view
            ),
        )
        self._capabilities_base.freeze()

    # ---------- public API ----------

    def get(self, plugin_id: str):
        if self._closed:
            raise RuntimeError("ExecutionContext is closed")

        runtime = self._runtime_for(plugin_id)
        return self._guarded_get(runtime, plugin_id)

    def close(self) -> None:
        if self._closed:
            return

        for runtime in self._runtimes.values():
            runtime.shutdown()

        if self._cleanup_sandbox:
            try:
                rmtree_best_effort(self._sandbox_root)
            except Exception:
                logger.exception("Failed to cleanup sandbox %s", self._sandbox_root)


        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # ---------- internal ----------

    def _runtime_for(self, plugin_id: str) -> ExecutionRuntime:
        base_id, _ = split_id_version(plugin_id)
        policy = self._dependency_overrides.get(plugin_id) or self._dependency_overrides.get(base_id) or self._root_policy
        return self._get_runtime(policy)

    def _policy_key(self, policy) -> tuple[Any, ...]:
        cls = policy.__class__
        mode = getattr(policy, "instance_mode", None)
        limits = getattr(policy, "limits", None)
        cfg = getattr(policy, "config", None) or {}
        config = dict(cfg)

        limits_key = repr(limits)
        try:
            config_key = json.dumps(config, sort_keys=True, default=str)
        except Exception:
            config_key = repr(config)

        return (cls, mode, limits_key, config_key)

    def _policy_for_scope(self, scope):
        if scope is DependencyScope.SINGLETON:
            return self._managed_policy
        if scope is DependencyScope.TRANSIENT:
            return self._ephemeral_policy
        if scope is DependencyScope.PLUGIN:
            raise NotImplementedError("DependencyScope.PLUGIN is not supported yet")
        return self._root_policy

    def _edge_filter_for(self, policy):
        current_key = self._policy_key(policy)

        def _f(parent_id: str, spec) -> bool:
            # 0) explicit override has higher priority than scope-based routing
            dep_raw = getattr(spec, "id", None)
            if dep_raw:
                dep_base, _ = split_id_version(dep_raw)
                override_policy = None
                if dep_raw in self._dependency_overrides:
                    override_policy = self._dependency_overrides[dep_raw]
                elif dep_base in self._dependency_overrides:
                    override_policy = self._dependency_overrides[dep_base]
                if override_policy is not None:
                    return self._policy_key(override_policy) == current_key

            scope = getattr(spec, "scope", DependencyScope.EXECUTION)
            target_policy = self._policy_for_scope(scope)
            return self._policy_key(target_policy) == current_key

        return _f

    def _get_runtime(self, policy) -> ExecutionRuntime:
        key = self._policy_key(policy)

        if key not in self._runtimes:
            resolver = DependencyResolver(
                registry=self._plugin_service.registry,
                edge_filter=self._edge_filter_for(policy),
            )

            rt = ExecutionRuntime(
                registry=self._plugin_service.registry,
                loader=self._plugin_service.loader,
                resolver=resolver,
                policy=policy,
                tracer=getattr(self._plugin_service, "tracer", None),
                dependency_getter=self._dependency_get,
                capabilities=self._capabilities_base,  # base provider; runtime wraps into policy-gated view
            )
            self._runtimes[key] = rt

        return self._runtimes[key]

    def _guarded_get(self, runtime: ExecutionRuntime, plugin_id: str):
        canon = runtime._canonical_key(plugin_id)
        token = (self._policy_key(runtime.policy), canon)

        if token in self._activation_stack:
            path = " -> ".join([x[1] for x in self._activation_stack] + [canon])
            raise DependencyCycleError(f"Cross-runtime dependency cycle: {path}")

        self._activation_stack.append(token)
        try:
            return runtime.get(plugin_id)
        finally:
            self._activation_stack.pop()


    def _dependency_get(self, spec):
        if not hasattr(spec, "id"):
            raise TypeError("Dependency getter expects DependencySpec")

        dep_id = spec.id

        # Capabilities are resolved in the runtime/tx path (policy-gated).
        # This fallback is still useful if someone calls it directly.
        if is_capability_ref(dep_id):
            try:
                return self.capabilities.get(dep_id)  # policy-gated view
            except Exception:
                if getattr(spec, "optional", False):
                    return None
                raise

        policy = self._policy_for_dependency(spec)
        runtime = self._get_runtime(policy)
        return self._guarded_get(runtime, spec.id)

    def _policy_for_dependency(self, spec) -> object:
        # 1. explicit override
        if spec.id in self._dependency_overrides:
            return self._dependency_overrides[spec.id]

        # 2. scope-based
        if spec.scope is DependencyScope.SINGLETON:
            return self._managed_policy

        if spec.scope is DependencyScope.EXECUTION:
            return self._root_policy

        if spec.scope is DependencyScope.TRANSIENT:
            return self._ephemeral_policy
        
        if spec.scope is DependencyScope.PLUGIN:
            raise NotImplementedError("DependencyScope.PLUGIN is not supported yet")

        return self._root_policy
