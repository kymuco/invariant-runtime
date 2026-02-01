# mypy: ignore-errors
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from invariant.exec.caps.registry import CAP_PREFIX, CapabilityProvider
from invariant.exec.caps.view import CapabilityView
from invariant.exec.runtime.activation import (
    ActivationEvent,
    ActivationEventType,
    ActivationTransaction,
)
from invariant.ops.metrics import RuntimeMetrics
from invariant.ops.supervisor import ExecutionSupervisor
from invariant.ops.tracing import InMemoryRuntimeTracer


def _ok(name: str) -> None:
    print(f"[PASS] {name}")


def _fail(name: str, e: Exception) -> None:
    print(f"[FAIL] {name}: {type(e).__name__}: {e}")
    raise


# ------------------ check 1: tracer enum fix ------------------

def check_tracer_activation_metrics() -> None:
    metrics = RuntimeMetrics()
    tr = InMemoryRuntimeTracer(metrics=metrics)

    tx = "tx1"
    root = "ik:test@1.0.0"

    t0 = time.time()
    tr.on_activation_event(ActivationEvent(tx_id=tx, event=ActivationEventType.START, root_id=root, plugin_id=None, timestamp=t0))
    tr.on_activation_event(ActivationEvent(tx_id=tx, event=ActivationEventType.END, root_id=root, plugin_id=None, timestamp=t0 + 0.123))

    snap = metrics.snapshot()
    by_root = snap["activation"]["duration_seconds_by_root"]
    if root not in by_root:
        raise AssertionError("metrics missing root label (enum comparison still broken?)")
    if by_root[root]["count"] != 1:
        raise AssertionError(f"expected count=1, got {by_root[root]['count']}")


# ------------------ check 2: rollback cleans ALL staged ------------------

class _KernelStub:
    def __init__(self) -> None:
        self.shutdown_force_calls: list[str] = []

    def shutdown_force(self, *, plugin: Any) -> None:
        self.shutdown_force_calls.append(getattr(plugin, "key", "<no-key>"))


class _StoreStub:
    def set(self, key: str, plugin: Any) -> None:
        pass


class _RuntimeStub:
    def __init__(self, caps: Any) -> None:
        self.kernel = _KernelStub()
        self.store = _StoreStub()
        self.capabilities = caps
        self._initialization_order: list[str] = []

    def _canonical_key(self, s: str) -> str:
        return s  # for test simplicity


class _Plugin:
    def __init__(self, key: str) -> None:
        self.key = key


def check_activation_rollback_cleans_all_staged() -> None:
    base_caps = CapabilityProvider(strict_versions=False)
    runtime = _RuntimeStub(caps=base_caps)

    tx = ActivationTransaction(runtime=runtime, root_id="root", tx_id="tx")
    tx.start()

    # staged but NOT ready
    tx.stage("A", _Plugin("A"))
    # staged and ready
    tx.stage("B", _Plugin("B"))
    tx.mark_ready("B")

    tx.rollback()

    calls = runtime.kernel.shutdown_force_calls
    if calls != ["B", "A"]:
        raise AssertionError(f"expected shutdown_force calls ['B','A'], got {calls}")


# ------------------ check 3: optional capability on deny returns None ------------------

@dataclass
class _Spec:
    id: str
    optional: bool = True


def check_optional_capability_deny_returns_none() -> None:
    base = CapabilityProvider(strict_versions=False)
    cap_id = f"{CAP_PREFIX}fs_sandbox"
    base.register(cap_id, object(), version="0.2.0")

    view = CapabilityView(base, deny={cap_id}, reason="denied for test")
    runtime = _RuntimeStub(caps=view)

    tx = ActivationTransaction(runtime=runtime, root_id="root", tx_id="tx2")
    got = tx.dependency_get(_Spec(id=cap_id, optional=True))
    if got is not None:
        raise AssertionError("expected None for optional denied capability")


# ------------------ check 4: supervisor plugin_call should NOT block create() globally ------------------

class _PluginSleep:
    def __init__(self, started_evt: threading.Event, sleep_s: float) -> None:
        self._evt = started_evt
        self._sleep = sleep_s

        class _FSM:
            plugin_id = "dummy"
        self.fsm = _FSM()

    def slow(self) -> str:
        self._evt.set()
        time.sleep(self._sleep)
        return "ok"


class _CtxStub:
    def __init__(self, plugin: Any) -> None:
        self._plugin = plugin
        self._closed = False

    def __enter__(self):
        return self

    def get(self, _plugin_id: str) -> Any:
        return self._plugin

    def close(self) -> None:
        self._closed = True


class _ServiceStub:
    def __init__(self, ctx_factory):
        self._ctx_factory = ctx_factory
        self.tracer = None  # supervisor checks .metrics off tracer optionally

    def execution(self, *, policy, dependency_overrides=None, cleanup_sandbox: bool = True):
        return self._ctx_factory()


def check_supervisor_create_not_blocked_by_plugin_call() -> None:
    started = threading.Event()

    plugin = _PluginSleep(started_evt=started, sleep_s=0.8)

    def ctx_factory():
        return _CtxStub(plugin)

    service = _ServiceStub(ctx_factory=ctx_factory)
    sup = ExecutionSupervisor(service, max_active_executions=64, default_ttl_seconds=300, cleanup_interval_seconds=1)

    # create execution 1
    eid1 = sup.create(policy=object())
    h = sup.plugin_get(execution_id=eid1, plugin_id="x")

    # run slow call in another thread
    exc: list[Exception] = []

    def _call():
        try:
            sup.plugin_call(execution_id=eid1, handle=h, method="slow")
        except Exception as e:
            exc.append(e)

    t = threading.Thread(target=_call, daemon=True)
    t.start()

    # wait until plugin method started (call is "inside")
    if not started.wait(timeout=2.0):
        raise AssertionError("slow method did not start in time")

    # create execution 2 while slow call still running
    t0 = time.perf_counter()
    eid2 = sup.create(policy=object())
    dt = time.perf_counter() - t0

    # if plugin_call held global lock, create() would likely block ~0.8s
    if dt > 0.35:
        raise AssertionError(f"create() took too long while plugin_call running: dt={dt:.3f}s (global lock still held?)")

    # cleanup
    sup.rollback(eid2)
    # slow call will finish and might auto-close eid1 if it threw; here it shouldn't
    t.join(timeout=2.0)
    if exc:
        raise AssertionError(f"unexpected exception in slow call: {exc[0]}")


def main() -> int:
    checks = [
        ("tracer_activation_metrics", check_tracer_activation_metrics),
        ("activation_rollback_cleans_all_staged", check_activation_rollback_cleans_all_staged),
        ("optional_capability_deny_returns_none", check_optional_capability_deny_returns_none),
        ("supervisor_create_not_blocked_by_plugin_call", check_supervisor_create_not_blocked_by_plugin_call),
    ]

    for name, fn in checks:
        try:
            fn()
            _ok(name)
        except Exception as e:
            _fail(name, e)

    print("All smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
