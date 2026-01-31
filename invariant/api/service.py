# invariant/api/service.py
from __future__ import annotations

from invariant.api.types import WorkspaceOptions
from invariant.exec.caps.registry import (
    CapabilityProvider,
    is_capability_ref,
    CapabilityNotFound,
    CapabilityUnavailableError,
    CapabilityVersionMismatch,
)
from invariant.exec.caps.inspector import RuntimeInspector
from invariant.exec.caps.ids import CAP_INSPECTOR, CAP_CONFIG_SOURCE
from invariant.exec.runtime.execution_runtime import ExecutionRuntime
from invariant.exec.runtime.execution_context import ExecutionContext
from invariant.exec.runtime.resolver import DependencyResolver
from invariant.api.policies import EphemeralPolicy, ManagedPolicy
from invariant.ops.ids import normalize_workspace_id


class PluginService:
    """
    High-level facade for plugin execution.

    Responsibilities:
    - provide managed plugin access
    - create execution contexts
    """

    def __init__(self, *, registry, loader, tracer=None):
        self.registry = registry
        self.loader = loader
        self.tracer = tracer

        self._managed_policy = ManagedPolicy()
        self._ephemeral_policy = EphemeralPolicy()

        self._managed_capabilities = CapabilityProvider(strict_versions=True)

        self._managed_runtime = ExecutionRuntime(
            registry=self.registry,
            loader=self.loader,
            resolver=DependencyResolver(registry=self.registry),
            policy=self._managed_policy,
            tracer=self.tracer,
            dependency_getter=self._managed_dependency_get,
            capabilities=self._managed_capabilities,
        )

        # Safe config source in managed runtime (env reading is policy-controlled; defaults are safe).
        from invariant.exec.caps.config_source import EnvDictConfigSource
        limits = getattr(self._managed_policy, "limits", None)
        config = getattr(self._managed_policy, "config", None) or {}
        self._managed_capabilities.register(
            CAP_CONFIG_SOURCE,
            EnvDictConfigSource(
                dict(config),
                allow_env=bool(getattr(limits, "allow_env_config", False)),
                env_prefix=str(getattr(limits, "config_env_prefix", "INVARIANT_") or "INVARIANT_"),
            ),
        )

        self._managed_capabilities.register(
            CAP_INSPECTOR,
            RuntimeInspector(
                registry=self.registry,
                runtime=self._managed_runtime,
                capabilities=self._managed_runtime.capabilities,  # <-- важно
            ),
        )
        self._managed_capabilities.freeze()

    # ---------- managed (singleton) ----------

    def get(self, plugin_id: str):
        """
        Return managed (singleton) plugin instance.
        """
        return self._managed_runtime.get(plugin_id)
    
    def shutdown(self, plugin_id: str) -> None:
        """
        Explicitly shutdown managed (singleton) plugin and drop it from cache.
        """
        self._managed_runtime.shutdown_plugin(plugin_id)

    def close(self) -> None:
        """
        Best-effort shutdown for service-owned runtimes.
        Useful for daemon shutdown / tests.
        """
        try:
            self._managed_runtime.shutdown()
        except Exception:
            pass

    # ---------- execution-scoped ----------

    def execution(self, *, policy, dependency_overrides=None, cleanup_sandbox: bool = True) -> ExecutionContext:
        """
        Create execution-scoped context.
        """
        return ExecutionContext(
            plugin_service=self,
            policy=policy,
            dependency_overrides=dependency_overrides,
            cleanup_sandbox=cleanup_sandbox,
        )
    
    def workspace(
        self,
        workspace_id: str,
        *,
        policy=None,
        dependency_overrides=None,
        options: WorkspaceOptions | None = None,
        cleanup_sandbox: bool = True,
    ) -> ExecutionContext:
        """
        Create an execution context bound to a persistent workspace.

        This is the ONLY supported way to use workspace_fs.
        """
        ws = normalize_workspace_id(workspace_id)
        if policy is None:
            policy = self._ephemeral_policy
        if options is None:
            options = WorkspaceOptions()

        return ExecutionContext(
            plugin_service=self,
            policy=policy,
            dependency_overrides=dependency_overrides,
            workspace_id=ws,
            workspace_options=options,
            cleanup_sandbox=cleanup_sandbox,
        )
    
    def _managed_dependency_get(self, spec):
        dep_id = spec.id
        if is_capability_ref(dep_id):
            try:
                # IMPORTANT: always go through policy-gated view (deny-list must apply)
                return self._managed_runtime.capabilities.get(dep_id)
            except (CapabilityNotFound, CapabilityUnavailableError, CapabilityVersionMismatch, ValueError):
                if getattr(spec, "optional", False):
                    return None
                raise
        return self._managed_runtime.get(dep_id)