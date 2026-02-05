# invariant/exec/runtime/kernel.py
from __future__ import annotations

import inspect
import logging

from invariant.exec.runtime.lifecycle import PluginFSM, PluginState
from invariant.exec.runtime.plugin_context import DefaultPluginContext
from invariant.load.loader import PluginLoader
from invariant.spec.descriptor import PluginDescriptor
from invariant.spec.errors import PluginLoadError
from invariant.spec.plugin import BasePlugin

log = logging.getLogger(__name__)


class PluginKernel:
    """
    Low-level lifecycle engine.

    Responsibilities:
    - instantiate plugin class
    - attach FSM
    - call initialize / shutdown safely
    """

    def create(
        self,
        *,
        descriptor: PluginDescriptor,
        loader: PluginLoader,
        context: DefaultPluginContext,
    ) -> BasePlugin:
        """
        Create plugin instance and attach FSM.
        No initialization happens here.
        """

        try:
            plugin = loader.load(descriptor)
        except Exception as e:
            raise PluginLoadError(
                f"Failed to load plugin {descriptor.meta.id}@{descriptor.meta.version}",
                meta_id=descriptor.meta.id,
                cause=e,
            ) from e

        # Attach FSM immediately
        plugin.fsm = PluginFSM(
            plugin_id=descriptor.meta.id,
            version=descriptor.meta.version,
        )

        # HARD invariant: meta + context are injected by the platform
        plugin.meta = descriptor.meta
        plugin.context = context

        return plugin

    def initialize(
        self,
        *,
        plugin: BasePlugin,
        dependencies: dict[str, object],
    ) -> None:
        """
        Initialize plugin with resolved dependencies.
        """

        fsm = plugin.fsm

        if fsm.state is not PluginState.CREATED:
            raise RuntimeError(
                f"Invalid initialize() call: plugin {fsm.plugin_id} "
                f"is in state {fsm.state}"
            )

        try:
            fsm.transition(PluginState.INITIALIZING)

            self._call_initialize(plugin, dependencies)

            fsm.transition(PluginState.READY)

        except Exception as e:
            fsm.transition(PluginState.FAILED, error=e)
            log.exception(
                "Plugin initialization failed: %s@%s",
                fsm.plugin_id,
                fsm.version,
            )
            raise

    def shutdown(self, *, plugin: BasePlugin) -> None:
        """
        Shutdown plugin safely.
        Idempotent on runtime level, but kernel enforces correctness.
        """

        fsm = plugin.fsm

        if fsm.state is PluginState.SHUTDOWN:
            return
        
        # IMPORTANT:
        # Even if plugin is FAILED, we still want best-effort cleanup.
        # FSM remains FAILED (terminal), but shutdown side-effects must be allowed.
        if fsm.state is PluginState.FAILED:
            self.shutdown_force(plugin=plugin)
            return

        try:
            fsm.transition(PluginState.SHUTTING_DOWN)

            if hasattr(plugin, "shutdown"):
                plugin.shutdown()
            self._platform_close_best_effort(plugin)

            fsm.transition(PluginState.SHUTDOWN)

        except Exception as e:
            fsm.transition(PluginState.FAILED, error=e)
            log.exception(
                "Plugin shutdown failed: %s@%s",
                fsm.plugin_id,
                fsm.version,
            )
            raise

    def shutdown_force(self, *, plugin: BasePlugin) -> None:
        """
        Best-effort cleanup path.
        - does NOT transition FSM (so it works even in terminal states like FAILED)
        - never raises (kernel containment)
        """
        try:
            if hasattr(plugin, "shutdown"):
                plugin.shutdown()
            self._platform_close_best_effort(plugin)
        except Exception:
            fsm = getattr(plugin, "fsm", None)
            pid = getattr(fsm, "plugin_id", "<unknown>")
            ver = getattr(fsm, "version", None)
            log.exception("Plugin force-shutdown failed: %s@%s", pid, ver)

    # ---------- internal ----------

    def _call_initialize(
        self,
        plugin: BasePlugin,
        deps: dict[str, object],
    ) -> None:
        """
        Call initialize() with safe signature binding.

        Rules:
        - deps dict is the source of truth (built from DependencySpec.param).
        - required parameters (no default) must be present in deps.
        - 'self' is ignored.
        - *args are not supported (raise).
        - **kwargs allows extra deps; otherwise extras are dropped.
        """

        sig = inspect.signature(plugin.initialize)

        params = list(sig.parameters.values())

        # Bound methods still show 'self' in signature; ignore it if present.
        if params and params[0].name == "self":
            params = params[1:]

        has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)

        # Disallow *args: it hides the contract (bad for invariants).
        if any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params):
            raise RuntimeError(
                f"{plugin.fsm.plugin_id}.initialize() must not use *args; "
                "use explicit named deps or **kwargs"
            )

        # Validate required named params
        for p in params:
            if p.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ):
                if p.default is inspect._empty and p.name not in deps:
                    raise RuntimeError(
                        f"Missing dependency '{p.name}' for plugin {plugin.fsm.plugin_id}"
                    )

        # Build kwargs for call
        if has_varkw:
            kwargs = dict(deps)
        else:
            allowed = {
                p.name
                for p in params
                if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
            }
            kwargs = {k: v for k, v in deps.items() if k in allowed}

        plugin.initialize(**kwargs)

    def _platform_close_best_effort(self, plugin: BasePlugin) -> None:
        """
        Platform-only cleanup hook for wrappers/proxies created by the platform.
        We intentionally do NOT call arbitrary .close() on user plugins.
        """
        try:
            if not getattr(plugin, "__invariant_close__", False):
                return
            close_fn = getattr(plugin, "close", None)
            if callable(close_fn):
                close_fn()
        except Exception:
            # never allow cleanup to crash shutdown path
            fsm = getattr(plugin, "fsm", None)
            pid = getattr(fsm, "plugin_id", "<unknown>")
            ver = getattr(fsm, "version", None)
            log.exception("Platform close failed: %s@%s", pid, ver)