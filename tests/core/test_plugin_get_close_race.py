"""Regression: an in-flight plugin_get owns the execution until get() exits.

These tests intentionally block inside real ExecutionSupervisor.plugin_get's
worker boundary. No sleeps used for race coordination except short bounded
timeout checks. ctx.close() is not allowed before the load has finished.
"""
from __future__ import annotations

import threading

from invariant.ops.host import RuntimeHost


class _BlockingContext:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.closed = threading.Event()
        self.close_count = 0
        self.closed_during_get = False

    def get(self, plugin_id: str):
        assert plugin_id == "tests:blocking"
        self.entered.set()
        if not self.release.wait(timeout=4):
            raise TimeoutError("test did not unblock get()")
        self.finished.set()
        return object()

    def close(self) -> None:
        self.close_count += 1
        if not self.finished.is_set():
            self.closed_during_get = True
        self.closed.set()


class _SingleContextService:
    tracer = None

    def __init__(self, ctx: _BlockingContext) -> None:
        self.ctx = ctx

    def execution(self, **_kwargs):
        return self.ctx

    def close(self):
        pass


def _host(ctx: _BlockingContext) -> RuntimeHost:
    host = RuntimeHost(
        plugin_service=_SingleContextService(ctx),
        enable_watchdog=False,
        max_active_executions=2,
    )
    host._supervisor._plugin_get_warn_s = 0
    return host


def _created(host: RuntimeHost) -> str:
    out = host.handle({"cmd": "create_execution", "policy": "ephemeral"})
    assert out["ok"] is True, out
    return out["execution"]


def test_rollback_while_plugin_get_initializes_must_defer_context_close():
    ctx = _BlockingContext()
    host = _host(ctx)
    execution_id = _created(host)
    observed = []

    thread = threading.Thread(
        target=lambda: observed.append(
            host.handle(
                {"cmd": "execution_plugin_get",
                 "execution": execution_id, "plugin": "tests:blocking"}
            )
        )
    )
    thread.start()
    try:
        assert ctx.entered.wait(timeout=3), "get() did not begin"
        out = host.handle({"cmd": "execution_rollback", "execution": execution_id})
        assert out["ok"] is True, out
        # The old implementation closes here, while get() is still running.
        assert not ctx.closed.is_set(), "context closed while get() was in flight"
    finally:
        ctx.release.set()
        thread.join(timeout=4)
        host.close()
    assert not thread.is_alive()
    assert ctx.closed.wait(timeout=3)
    assert not ctx.closed_during_get
    assert ctx.close_count == 1
    assert len(observed) == 1 and observed[0]["ok"] is False


def test_plugin_get_watchdog_timeout_does_not_close_context_under_loader():
    ctx = _BlockingContext()
    host = _host(ctx)
    host._supervisor._plugin_get_timeout_s = 0.04
    execution_id = _created(host)
    try:
        out = host.handle(
            {"cmd": "execution_plugin_get",
             "execution": execution_id, "plugin": "tests:blocking"}
        )
        assert ctx.entered.is_set(), "get() worker did not run"
        assert out["ok"] is False and out["error"] == "timeout", out
        assert not ctx.closed.is_set(), "timeout closed context during get()"
    finally:
        ctx.release.set()
        assert ctx.closed.wait(timeout=3), "deferred close was never finalized"
        host.close()
    assert not ctx.closed_during_get
    assert ctx.close_count == 1


def test_successful_get_releases_its_lease_before_explicit_rollback():
    ctx = _BlockingContext()
    ctx.release.set()
    host = _host(ctx)
    execution_id = _created(host)
    try:
        out = host.handle(
            {"cmd": "execution_plugin_get",
             "execution": execution_id, "plugin": "tests:blocking"}
        )
        assert out["ok"] is True, out
        assert ctx.finished.is_set()
        assert not ctx.closed.is_set()
        rolled_back = host.handle(
            {"cmd": "execution_rollback", "execution": execution_id}
        )
        assert rolled_back["ok"] is True, rolled_back
        assert ctx.closed.is_set()
        assert not ctx.closed_during_get
        assert ctx.close_count == 1
    finally:
        host.close()
