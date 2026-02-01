# ruff: noqa: E402
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# Support both layouts:
# 1) repo_root/invariant/...
# 2) repo_root/src/invariant/...
for p in (ROOT, SRC):
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))


import socket
import threading
import time

import pytest

from invariant.ops.host import RuntimeHost
from invariant.ops.metrics import RuntimeMetrics
from invariant.ops.tracing import InMemoryRuntimeTracer
from tests.fake_service import FakePluginService


@pytest.fixture()
def metrics():
    return RuntimeMetrics()


@pytest.fixture()
def tracer(metrics):
    return InMemoryRuntimeTracer(metrics=metrics)


@pytest.fixture()
def fake_service(tracer):
    return FakePluginService(tracer=tracer)


@pytest.fixture()
def runtime_host(fake_service):
    # watchdog выключаем: в тестах хотим детерминизм
    return RuntimeHost(
        plugin_service=fake_service,
        enable_watchdog=False,
        max_active_executions=8,
        default_ttl_seconds=1,
        cleanup_interval_seconds=1,
    )


@pytest.fixture()
def daemon_server(runtime_host):
    """
    Поднимаем реальный HTTP server из invariant.ops.daemon.
    ВНИМАНИЕ: эти тесты намеренно проверяют контракт handler<->server.
    Если у вас сейчас несовместимость (а она по коду выглядит вероятной),
    часть стресс-тестов будет падать/скипаться до фикса.
    """
    from invariant.ops.daemon import InvariantHTTPServer, JsonAPIHandler

    server = InvariantHTTPServer(
        ("127.0.0.1", 0),
        JsonAPIHandler,
        runtime_host=runtime_host,
        admin_token="secret",
        max_body_bytes=1024 * 1024,
        log_request_bodies=False,
        max_concurrent_requests=8,
        acquire_timeout_s=0.05,
        retry_after_s=1,
    )

    t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()

    # Wait until the socket is actually accepting connections (Windows can be slow under load)
    host, port = server.server_address
    deadline = time.time() + 2.0
    last_err = None
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                last_err = None
                break
        except OSError as e:
            last_err = e
            time.sleep(0.02)
    if last_err is not None:
        raise RuntimeError(f"Server did not start listening in time: {last_err}") from last_err

    try:
        yield server
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
        t.join(timeout=2)
