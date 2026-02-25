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

_SUITE_PATHS: dict[str, tuple[str, ...]] = {
    "core": ("tests/core/",),
    "agent_factory": ("tests/agent_factory/", "tests/e2e/"),
    "e2e": ("tests/e2e/",),
    "plugin_packs": (
        "tests/plugin_packs_core/",
        "tests/plugin_packs_agent_factory/",
        "tests/plugin_packs_research/",
    ),
    "plugin_packs_core": ("tests/plugin_packs_core/",),
    "plugin_packs_agent_factory": ("tests/plugin_packs_agent_factory/",),
    "plugin_packs_research": ("tests/plugin_packs_research/",),
}


def pytest_addoption(parser):
    parser.addoption(
        "--suite",
        action="store",
        default="all",
        help=(
            "Logical suite selector: all, core, agent_factory, e2e, plugin_packs, "
            "plugin_packs_core, plugin_packs_agent_factory, plugin_packs_research."
        ),
    )


def _item_relpath(item: pytest.Item) -> str:
    p = Path(str(item.fspath)).resolve()
    try:
        rel = p.relative_to(ROOT).as_posix()
    except ValueError:
        rel = p.as_posix()
    return rel


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    suite = str(config.getoption("--suite") or "all").strip()
    if suite == "all":
        return
    prefixes = _SUITE_PATHS.get(suite)
    if prefixes is None:
        valid = ", ".join(["all", *sorted(_SUITE_PATHS.keys())])
        raise pytest.UsageError(f"Unknown --suite '{suite}'. Valid values: {valid}")

    selected: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        rel = _item_relpath(item)
        if any(rel.startswith(prefix) for prefix in prefixes):
            selected.append(item)
        else:
            deselected.append(item)

    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected


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
