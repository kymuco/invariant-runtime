# invariant/api/service.py
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Self

from invariant.api.policies import EphemeralPolicy, ManagedPolicy
from invariant.api.types import PluginInfo, WorkspaceOptions
from invariant.exec.caps.ids import CAP_CONFIG_SOURCE, CAP_INSPECTOR
from invariant.exec.caps.inspector import RuntimeInspector
from invariant.exec.caps.registry import (
    CapabilityNotFound,
    CapabilityProvider,
    CapabilityUnavailableError,
    CapabilityVersionMismatch,
    is_capability_ref,
)
from invariant.exec.runtime.execution_context import ExecutionContext
from invariant.exec.runtime.execution_runtime import ExecutionRuntime
from invariant.exec.runtime.resolver import DependencyResolver
from invariant.load.env.env_manager import EnvManager
from invariant.ops.ids import normalize_workspace_id
from invariant.spec.errors import AmbiguousPluginReference, PluginNotFoundError
from invariant.spec.versioning import Version


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
        self.env_manager = EnvManager()

        # Best-effort: wire EnvManager into ManifestSource instances so isolated deps work
        srcs = getattr(loader, "sources", None)
        if isinstance(srcs, list):
            for src in srcs:
                try:
                    if getattr(src, "kind", None) == "manifest" and getattr(src, "env_manager", None) is None:
                        src.env_manager = self.env_manager
                except Exception:
                    pass

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
                capabilities=self._managed_runtime.capabilities,
            ),
        )
        self._managed_capabilities.freeze()

    @classmethod
    def from_manifest_paths(
        cls,
        search_paths: Iterable[str | Path],
        *,
        include_unstable: bool = False,
        fail_fast: bool = True,
        tracer: Any | None = None,
    ) -> Self:
        """
        Build a service from one or more directories containing ``manifest.json`` files.

        This is the recommended host entry point for manifest-based plugins. Discovery
        and registration use strict mode, so every discovered plugin must declare an
        explicit version.
        """
        paths = [Path(path) for path in search_paths]
        if not paths:
            raise ValueError("search_paths must contain at least one path")

        # Keep loader/registry/source machinery behind the public facade. They remain
        # implementation-level surfaces in the 0.1 compatibility contract.
        from invariant.catalog.registry import PluginRegistry
        from invariant.load.loader import PluginLoader
        from invariant.load.sources.manifest import ManifestSource

        registry = PluginRegistry(mode="strict")
        loader = PluginLoader([ManifestSource(paths)])

        descriptors = loader.discover(
            include_unstable=include_unstable,
            mode="strict",
            fail_fast=fail_fast,
        )
        for descriptor in descriptors:
            registry.register(descriptor)

        return cls(registry=registry, loader=loader, tracer=tracer)

    # ---------- inventory ----------

    def list_plugins(self, *, category: str | None = None) -> tuple[PluginInfo, ...]:
        """Return an immutable host-facing snapshot of discovered plugin builds."""
        infos = [
            PluginInfo(
                id=descriptor.meta.id,
                version=descriptor.meta.version,
                name=descriptor.meta.name,
                maturity=descriptor.meta.maturity or "stable",
                categories=tuple(descriptor.meta.categories or ()),
            )
            for descriptor in self.registry.list(category=category)
        ]

        def _sort_key(info: PluginInfo) -> tuple[str, bool, Version]:
            version = Version(info.version) if info.version is not None else Version("0.dev0")
            return (info.id, info.version is not None, version)

        return tuple(sorted(infos, key=_sort_key))

    def has_plugin(self, plugin_id: str) -> bool:
        """Return whether a plugin reference names at least one discovered build."""
        try:
            self.registry.resolve(plugin_id)
        except PluginNotFoundError:
            return False
        except AmbiguousPluginReference:
            # Multiple local builds still mean the base plugin exists; activation may
            # require an exact version to disambiguate them.
            return True
        return True

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

    def execution(
        self,
        *,
        policy,
        dependency_overrides=None,
        workspace_id: str | None = None,
        workspace_options: WorkspaceOptions | dict[str, Any] | None = None,
        workspace_path: str | None = None,
        cleanup_sandbox: bool = True,
    ) -> ExecutionContext:
        """
        Create execution-scoped context.
        """
        opts: WorkspaceOptions | None
        if workspace_options is None:
            opts = None
        elif isinstance(workspace_options, WorkspaceOptions):
            opts = workspace_options
        elif isinstance(workspace_options, dict):
            opts = WorkspaceOptions(**workspace_options)
        else:
            raise TypeError("workspace_options must be WorkspaceOptions|dict|None")

        return ExecutionContext(
            plugin_service=self,
            policy=policy,
            dependency_overrides=dependency_overrides,
            workspace_id=workspace_id,
            workspace_options=opts,
            workspace_path=workspace_path,
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

        return self.execution(
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
