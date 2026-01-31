# invariant/exec/activation.py
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Callable

from invariant.spec.plugin import BasePlugin
from invariant.exec.caps.registry import CapabilityNotFound, CapabilityVersionMismatch, is_capability_ref
from invariant.exec.caps.registry import CapabilityUnavailableError


class ActivationEventType(Enum):
    START = auto()
    STAGE = auto()
    READY = auto()
    COMMIT = auto()
    ROLLBACK = auto()
    SHUTDOWN = auto()
    END = auto()


@dataclass(frozen=True)
class ActivationEvent:
    tx_id: str
    event: ActivationEventType
    root_id: str
    plugin_id: str | None
    timestamp: float
    detail: str | None = None


class ActivationTracer:
    """Simple in-process tracer interface."""
    def on_activation_event(self, e: ActivationEvent) -> None:  # pragma: no cover
        raise NotImplementedError


class DependencyNotReady(RuntimeError):
    def __init__(self, dep_key: str):
        super().__init__(f"Dependency is staged but not READY yet: {dep_key}")
        self.dep_key = dep_key


@dataclass
class ActivationTransaction:
    """
    Transactional activation for a dependency graph.

    Guarantees:
    - Do not pollute store unless the whole activation succeeds.
    - On failure, shutdown anything initialized (READY) in reverse order.

    Notes:
    - "created" plugins that never reached READY are simply discarded.
    """

    runtime: "ExecutionRuntime"  # forward  # type: ignore
    root_id: str
    tx_id: str

    staged: Dict[str, BasePlugin] = field(default_factory=dict)
    ready_order: List[str] = field(default_factory=list)
    ready_set: set[str] = field(default_factory=set)
    created_order: List[str] = field(default_factory=list)  # stage order (for rollback)

    # Delegates "external dependencies" (other policies/runtimes) back to context getter
    base_dependency_getter: Optional[Callable[[Any], object]] = None

    def _emit(self, event: ActivationEventType, *, plugin_id: str | None = None, detail: str | None = None) -> None:
        tracer = getattr(self.runtime, "tracer", None)
        if tracer is None:
            return
        cb = getattr(tracer, "on_activation_event", None)
        if cb is None:
            return
        try:
            cb(
                ActivationEvent(
                    tx_id=self.tx_id,
                    event=event,
                    root_id=self.root_id,
                    plugin_id=plugin_id,
                    timestamp=time.time(),
                    detail=detail,
                )
            )
        except Exception:
            return

    def start(self) -> None:
        self._emit(ActivationEventType.START)

    def end(self) -> None:
        self._emit(ActivationEventType.END)

    def get_staged(self, plugin_key: str) -> Optional[BasePlugin]:
        return self.staged.get(plugin_key)

    def dependency_get(self, spec) -> Optional[Any]:
        dep_ref = getattr(spec, "id", None)
        if not dep_ref:
            raise TypeError("Dependency getter expects DependencySpec with .id")

        # 0) Capabilities
        if is_capability_ref(dep_ref):
            try:
                return self.runtime.capabilities.get(dep_ref)
            except (CapabilityNotFound, CapabilityUnavailableError, CapabilityVersionMismatch, ValueError):
                if getattr(spec, "optional", False):
                    return None
                raise

        # 1) Plugin dependency path (transaction-visible)
        try:
            key = self.runtime._canonical_key(dep_ref)
        except Exception:
            if getattr(spec, "optional", False):
                return None
            raise

        staged = self.staged.get(key)
        if staged is not None:
            if key in self.ready_set:
                return staged
            if getattr(spec, "optional", False):
                return None
            raise DependencyNotReady(key)

        # Not staged: delegate to base getter (ExecutionContext routing)
        if self.base_dependency_getter is not None:
            return self.base_dependency_getter(spec)

        # Fallback (single-runtime only)
        return self.runtime.store.get(key)

    def stage(self, plugin_key: str, plugin: BasePlugin) -> None:
        if plugin_key in self.staged:
            raise RuntimeError(f"Duplicate stage: {plugin_key}")
        self.staged[plugin_key] = plugin
        self.created_order.append(plugin_key)
        self._emit(ActivationEventType.STAGE, plugin_id=plugin_key)

    def mark_ready(self, plugin_key: str) -> None:
        if plugin_key not in self.staged:
            raise RuntimeError(f"mark_ready called for non-staged: {plugin_key}")
        self.ready_order.append(plugin_key)
        self.ready_set.add(plugin_key)
        self._emit(ActivationEventType.READY, plugin_id=plugin_key)

    def commit(self) -> None:
        for key in self.ready_order:
            plugin = self.staged.get(key)
            if plugin is None:
                continue
            self.runtime.store.set(key, plugin)
            self.runtime._initialization_order.append(key)

        self._emit(ActivationEventType.COMMIT, detail=f"count={len(self.ready_order)}")

        self.staged.clear()
        self.ready_order.clear()
        self.ready_set.clear()
        self.created_order.clear()

    def rollback(self) -> None:
        self._emit(ActivationEventType.ROLLBACK, detail=f"ready_count={len(self.ready_order)}")
        # IMPORTANT:
        # Rollback must cleanup EVERYTHING created in the tx, not only READY ones.
        for pid in reversed(self.created_order):
            plugin = self.staged.get(pid)
            if not plugin:
                continue
            try:
                shutdown_force = getattr(self.runtime.kernel, "shutdown_force", None)
                if callable(shutdown_force):
                    shutdown_force(plugin=plugin)
                else:
                    self.runtime.kernel.shutdown(plugin=plugin)
                self._emit(ActivationEventType.SHUTDOWN, plugin_id=pid)
            except Exception:
                # best-effort containment
                pass

        self.staged.clear()
        self.ready_order.clear()
        self.ready_set.clear()
        self.created_order.clear()
