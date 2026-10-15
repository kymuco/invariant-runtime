"""RuntimeHost terminal shutdown must own contexts, not abandon them.

Real RuntimeHost -> ExecutionSupervisor -> context paths; deterministic
threading Events, no elapsed-time sleeps or GPU-specific fixtures.
"""
from __future__ import annotations

import threading

from invariant.ops.host import RuntimeHost


class _BlockingPlugin:
    def __init__(self, ctx) -> None:
        self.ctx = ctx

    def run(self) -> str:
        self.ctx.started.set()
        if not self.ctx.release.wait(timeout=4):
            raise AssertionError("blocked plugin call was never released")
        if self.ctx.closed.is_set():
            raise AssertionError("execution resource freed during plugin call")
        return "ok"


class _ObservedContext:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.closed = threading.Event()
        self.close_count = 0
        self.plugin = _BlockingPlugin(self)

    def get(self, plugin_id):
        assert plugin_id == "tests:blocking"
        return self.plugin

    def close(self):
        self.close_count += 1
        self.closed.set()


class _ObservedService:
    tracer = None

    def __init__(self):
        self.contexts = []
        self.closed = threading.Event()
        self.close_count = 0

    def execution(self, **_kwargs):
        ctx = _ObservedContext()
        self.contexts.append(ctx)
        return ctx

    def close(self):
        self.close_count += 1
        self.closed.set()


def _host():
    service = _ObservedService()
    host = RuntimeHost(
        plugin_service=service,
        enable_watchdog=False,
        plugin_call_workers=2,
        default_plugin_call_timeout_s=3,
    )
    return host, service


def _start_execution(host):
    created = host.handle({"cmd": "create_execution", "policy": "ephemeral"})
    assert created["ok"], created
    return created["execution"]


def test_host_close_releases_idle_execution_before_service():
    host, service = _host()
    execution_id = _start_execution(host)
    ctx = service.contexts[0]
    assert not ctx.closed.is_set()

    host.close()

    assert ctx.closed.is_set(), "idle execution context leaked after host.close"
    assert ctx.close_count == 1
    assert service.closed.is_set()
    assert service.close_count == 1
    assert execution_id not in host._supervisor._executions


def test_host_close_defers_context_and_service_while_plugin_running():
    host, service = _host()
    execution_id = _start_execution(host)
    retrieved = host.handle({
        "cmd": "execution_plugin_get",
        "execution": execution_id,
        "plugin": "tests:blocking",
    })
    assert retrieved["ok"], retrieved
    ctx = service.contexts[0]
    response = []

    thread = threading.Thread(
        target=lambda: response.append(host.handle({
            "cmd": "execution_plugin_call",
            "execution": execution_id,
            "handle": retrieved["handle"],
            "method": "run",
        }))
    )
    thread.start()
    try:
        assert ctx.started.wait(3), "plugin did not start"
        host.close()  # must not wait for an indefinitely running plugin
        assert not ctx.closed.is_set(), "context closed while call was in flight"
        assert not service.closed.is_set(), "service closed before active context"
        assert execution_id not in host._supervisor._executions
    finally:
        ctx.release.set()
        thread.join(timeout=4)
        host.close()

    assert not thread.is_alive()
    assert response == [{"ok": True, "result": "ok"}], response
    assert ctx.closed.wait(timeout=2)
    assert service.closed.wait(timeout=2)
    assert ctx.close_count == service.close_count == 1


def test_cancelled_queued_plugin_call_releases_execution_drain():
    import concurrent.futures
    import time

    host, service = _host()
    # The outer executor can accept two requests; the inner timeout pool
    # has only one worker so its second invocation stays queued.
    host._supervisor._timeout_pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=1
    )
    first = _start_execution(host)
    second = _start_execution(host)
    first_ctx, second_ctx = service.contexts
    first_h = host.handle({
        "cmd": "execution_plugin_get",
        "execution": first,
        "plugin": "tests:blocking",
    })
    second_h = host.handle({
        "cmd": "execution_plugin_get",
        "execution": second,
        "plugin": "tests:blocking",
    })
    assert first_h["ok"] and second_h["ok"]

    observed = []
    thread_a = threading.Thread(target=lambda: observed.append(
        host.handle({
            "cmd": "execution_plugin_call", "execution": first,
            "handle": first_h["handle"], "method": "run", "timeout_s": 3,
        })
    ))
    thread_b = threading.Thread(target=lambda: observed.append(
        host.handle({
            "cmd": "execution_plugin_call", "execution": second,
            "handle": second_h["handle"], "method": "run", "timeout_s": 3,
        })
    ))
    thread_a.start()
    try:
        assert first_ctx.started.wait(timeout=2)
        thread_b.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with host._supervisor._lock:
                session = host._supervisor._executions.get(second)
            if session is not None:
                with session._lock:
                    if session._inflight_calls == 1:
                        break
            time.sleep(0.005)
        else:
            raise AssertionError("second request was not queued in supervisor")

        host.close()
        # The queue shutdown cancels second future before _run() starts;
        # its context still must finalize instead of leaking forever.
        assert second_ctx.closed.wait(timeout=2)
        assert not first_ctx.closed.is_set()
        assert not service.closed.is_set()
    finally:
        first_ctx.release.set()
        second_ctx.release.set()
        thread_a.join(timeout=4)
        thread_b.join(timeout=4)
        host.close()

    assert not thread_a.is_alive() and not thread_b.is_alive()
    assert first_ctx.closed.wait(timeout=2)
    assert service.closed.wait(timeout=2)
    assert first_ctx.close_count == second_ctx.close_count == 1
    assert service.close_count == 1


def test_host_close_is_idempotent_and_rejects_new_execution():
    host, service = _host()
    host.close()
    host.close()
    assert service.close_count == 1
    new = host.handle({"cmd": "create_execution", "policy": "ephemeral"})
    assert new["ok"] is False, new
    assert not service.contexts, "new execution was admitted after host.close"
