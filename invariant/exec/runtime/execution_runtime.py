# invariant/exec/runtime/execution_runtime.py
from __future__ import annotations

from types import MappingProxyType
from typing import Dict, List, Any, Mapping, Optional
import logging
import uuid

from invariant.spec.plugin import BasePlugin
from invariant.spec.dependency import DependencySpec
from invariant.exec.runtime.activation import ActivationTransaction
from invariant.exec.runtime.plugin_context import DefaultPluginContext
from invariant.exec.runtime.kernel import PluginKernel
from invariant.exec.caps.registry import (
    CapabilityNotFound,
    CapabilityProvider,
    CapabilityUnavailableError,
    CapabilityVersionMismatch,
    is_capability_ref
)
from invariant.exec.caps.view import CapabilityView
from invariant.exec.caps.audit import CapabilityAudit
from invariant.exec.caps.policy import CapabilityPolicy, CapabilityPolicyContext
from invariant.spec.modes import InstanceMode


log = logging.getLogger(__name__)


class ExecutionRuntime:
    """
    Policy-safe execution engine.

    Construction order (invariants):
    1) runtime identity
    2) context (emit_event depends on runtime_id)
    3) core subsystems (kernel/store)
    4) capabilities (policy gating + audit binding)
    """

    def __init__(
        self,
        *,
        registry,
        loader,
        resolver,
        policy,
        tracer=None,
        dependency_getter=None,
        capabilities: CapabilityProvider | None = None,
    ) -> None:
        # ---- 1) identity/state ----
        self.runtime_id = str(uuid.uuid4())
        self._closed = False
        self._initialization_order: List[str] = []

        # ---- 2) basic wiring ----
        self.registry = registry
        self.loader = loader
        self.resolver = resolver
        self.policy = policy
        self.tracer = tracer
        self._dependency_getter = dependency_getter or self.dependency_get

        # ---- 3) context (must exist before audit) ----
        self.context = DefaultPluginContext(
            runtime_id=self.runtime_id,
            config=MappingProxyType({}),
            emit_event=self._emit_runtime_event,  # stable method, not inner closure
        )

        # ---- 4) core subsystems ----
        self.kernel = PluginKernel()
        self.store = policy.create_store(registry=registry)

        # ---- 5) capabilities (base -> policy gate -> audit binder) ----
        base_caps = capabilities or CapabilityProvider(strict_versions=False, strict_catalog=True)
        self._cap_provider = base_caps

        cap_policy = getattr(policy, "capability_policy", None)
        if cap_policy is None:
            cap_policy = CapabilityPolicy()
        self.capability_policy = cap_policy

        rules = cap_policy.build_view_rules(
            ctx=CapabilityPolicyContext(
                instance_mode=getattr(policy, "instance_mode", None),
                policy_name=getattr(policy, "name", None),
                limits=getattr(policy, "limits", None),
            )
        )

        self._cap_audit = CapabilityAudit(emit=self.context.emit_event)
        self._cap_bind_cache: Dict[str, object] = {}

        self.capabilities = CapabilityView(
            base_caps,
            deny=rules["deny"],
            allow=rules["allow"],
            deny_prefixes=rules.get("deny_prefixes", ()),
            reason=rules["reason"],
            binder=self._bind_capability,  # no inline function
        )

    # ---------------- internals ----------------

    def _emit_runtime_event(
        self,
        name: str,
        payload: Optional[Mapping[str, Any]] = None,
        **fields: Any,
    ) -> None:
        """
        Backward-compatible plugin event API.

        Supported:
          - emit_event(name, payload_dict)
          - emit_event(name, payload=payload_dict)
          - emit_event(name, **fields)

        Platform-reserved:
          - runtime_id is injected by platform; plugin-supplied runtime_id is ignored.

        Containment: never raises to plugin code.
        """
        tracer = self.tracer
        cb = getattr(tracer, "on_runtime_event", None) if tracer else None
        if cb is None and tracer is not None:
            # optional compat alias
            cb = getattr(tracer, "emit_event", None)
        if cb is None:
            return
        try:
            data: Dict[str, Any] = {}

            # Start with payload (old style)
            if payload:
                data.update(dict(payload))

            # Then overlay explicit kwargs (new style)
            if fields:
                # If caller passed payload=... via kwargs, merge it too.
                pl = fields.pop("payload", None)
                if pl is not None:
                    if isinstance(pl, Mapping):
                        data.update(dict(pl))
                    else:
                        # ignore invalid payload type (containment)
                        pass
                data.update(fields)

            # platform-reserved key
            data.pop("runtime_id", None)

            cb(name, runtime_id=self.runtime_id, **data)
        except Exception:
            return

    def _bind_capability(self, cap_id: str, value: object) -> object:
        """
        Returns runtime-bound capability instance.
        - attaches audit sink if capability supports bind_audit()
        - keeps stable identity per (runtime, cap_id)
        - containment: binding never raises to caller
        """
        cached = self._cap_bind_cache.get(cap_id)
        if cached is not None:
            return cached

        bind_fn = getattr(value, "bind_audit", None)
        if callable(bind_fn):
            try:
                bound = bind_fn(self._cap_audit, cap_id=cap_id)
                self._cap_bind_cache[cap_id] = bound
                return bound
            except Exception:
                # fall back to raw value
                pass

        self._cap_bind_cache[cap_id] = value
        return value

    # ---------------- public API ----------------

    def dependency_get(self, spec: DependencySpec) -> object:
        dep_ref = spec.id

        if is_capability_ref(dep_ref):
            try:
                return self.capabilities.get(dep_ref)
            except (CapabilityNotFound, CapabilityUnavailableError, CapabilityVersionMismatch, ValueError):
                if spec.optional:
                    return None
                raise

        return self.get(dep_ref)

    def get(self, plugin_id: str) -> BasePlugin:
        if not isinstance(plugin_id, str):
            raise TypeError(
                f"ExecutionRuntime.get() expects plugin_id: str, got {type(plugin_id).__name__}"
            )
        if self._closed:
            raise RuntimeError("ExecutionRuntime is closed")

        root_key = self._canonical_key(plugin_id)

        existing = self.store.get(root_key)
        if existing:
            if not self._is_ready(existing):
                raise RuntimeError(f"Plugin {root_key} is in state {existing.fsm.state}")
            return existing

        # Collect dependency graph for THIS policy/runtime only (resolver has edge_filter)
        self.resolver.collect(plugin_id)
        order = self.resolver.execution_order()

        tx = ActivationTransaction(runtime=self, root_id=plugin_id, tx_id=str(uuid.uuid4()))
        tx.base_dependency_getter = self._dependency_getter
        tx.start()

        try:
            for pid in order:
                pid_key = self._canonical_key(pid)

                committed = self.store.get(pid_key)
                if committed:
                    if not self._is_ready(committed):
                        raise RuntimeError(f"Plugin {pid_key} is in state {committed.fsm.state}")
                    continue

                if tx.get_staged(pid_key) is not None:
                    continue

                self._create_and_init(pid, pid_key=pid_key, tx=tx, dependency_getter=tx.dependency_get)

            tx.commit()

        except Exception as e:
            # try:
            #     tx._emit(ActivationEventType.ROLLBACK, detail=f"error={type(e).__name__}: {e}")
            # except Exception:
            #     pass
            tx.rollback()
            raise

        finally:
            tx.end()

        plugin = self.store.get(root_key)
        if not plugin:
            raise RuntimeError(f"Plugin {root_key} was not created")
        if not self._is_ready(plugin):
            raise RuntimeError(f"Plugin {root_key} is not READY after commit")
        return plugin

    def shutdown(self) -> None:
        if self._closed:
            return

        # Managed runtime does NOT own singleton instances (registry owns them).
        if getattr(self.policy, "instance_mode", None) == InstanceMode.MANAGED:
            self._initialization_order.clear()
            self._closed = True
            return

        for key in reversed(self._initialization_order):
            plugin = self.store.get(key)
            if not plugin:
                continue
            try:
                self.kernel.shutdown(plugin=plugin)
            except Exception:
                log.exception("Failed to shutdown plugin %s", key)

        self.store.clear()
        self._initialization_order.clear()
        self._closed = True

    def shutdown_plugin(self, plugin_id: str) -> None:
        if self._closed:
            raise RuntimeError("ExecutionRuntime is closed")

        key = self._canonical_key(plugin_id)
        plugin = self.store.get(key)
        if not plugin:
            return

        self.kernel.shutdown(plugin=plugin)
        drop = getattr(self.store, "drop", None)
        if callable(drop):
            drop(key)

        try:
            self._initialization_order.remove(key)
        except ValueError:
            pass

    # ---------- internal ----------

    def _create_and_init(
        self,
        plugin_id: str,
        *,
        pid_key: str,
        tx: ActivationTransaction | None = None,
        dependency_getter=None,
    ) -> None:
        descriptor = self.registry.resolve(plugin_id)
        dep_instances: Dict[str, object] = {}

        getter = dependency_getter or self._dependency_getter

        for spec in descriptor.meta.dependencies:
            instance = getter(spec)
            if instance is None and not spec.optional:
                raise RuntimeError(
                    f"Missing dependency {spec.id} for plugin {descriptor.meta.id}"
                )
            dep_instances[spec.param] = instance

        plugin = self.kernel.create(
            descriptor=descriptor,
            loader=self.loader,
            context=self.context,
        )

        if self.tracer:
            plugin.fsm.on_event(self.tracer.on_plugin_event)

        if tx is not None:
            tx.stage(pid_key, plugin)

        self.kernel.initialize(plugin=plugin, dependencies=dep_instances)

        if tx is not None:
            tx.mark_ready(pid_key)
            return

        self.store.set(pid_key, plugin)
        self._initialization_order.append(pid_key)

    def _canonical_key(self, plugin_ref: str) -> str:
        """
        Canonical instance key: always 'id@pinned_version'.
        Registry MUST return a pinned descriptor (meta.version is set),
        including dev local versions.
        """
        if not isinstance(plugin_ref, str) or not plugin_ref.strip():
            raise ValueError(f"Invalid plugin ref: {plugin_ref!r}")

        d = self.registry.resolve(plugin_ref)
        v = getattr(getattr(d, "meta", None), "version", None)

        if not v:
            raise RuntimeError(
                f"Registry resolved descriptor without pinned version: {d.meta.id!r}"
            )

        return f"{d.meta.id}@{v}"

    def _is_ready(self, plugin: BasePlugin) -> bool:
        fsm = getattr(plugin, "fsm", None)
        if fsm is None:
            return True
        ready = getattr(fsm, "is_ready", None)
        if callable(ready):
            return bool(ready())
        return bool(ready)
