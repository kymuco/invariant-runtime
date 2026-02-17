from __future__ import annotations

import concurrent.futures
import time

import pytest


def test_host_happy_path(runtime_host):
    r = runtime_host.handle({"cmd": "create_execution", "policy": "ephemeral", "ttl_seconds": 2})
    assert r["ok"] is True
    exec_id = r["execution"]

    h = runtime_host.handle({"cmd": "execution_plugin_get", "execution": exec_id, "plugin": "tests:echo"})["handle"]
    out = runtime_host.handle(
        {"cmd": "execution_plugin_call", "execution": exec_id, "handle": h, "method": "echo", "args": ["hi"]}
    )
    assert out["ok"] is True
    assert out["result"] == "hi"

    assert runtime_host.handle({"cmd": "execution_commit", "execution": exec_id})["ok"] is True


def test_ttl_cleanup_is_real(runtime_host):
    r = runtime_host.handle({"cmd": "create_execution", "policy": "ephemeral", "ttl_seconds": 1})
    assert r["ok"] is True
    exec_id = r["execution"]

    # делаем активность
    runtime_host.handle({"cmd": "execution_plugin_get", "execution": exec_id, "plugin": "tests:sleep"})

    time.sleep(1.2)

    # cleanup_expired должен снести сессию (и это то, что будет происходить в проде)
    cleaned = runtime_host.handle({"cmd": "cleanup_expired"})
    assert cleaned["ok"] is True
    assert cleaned["cleaned"] >= 1

    # дальнейшие обращения должны быть отказаны
    r2 = runtime_host.handle({"cmd": "execution_plugin_get", "execution": exec_id, "plugin": "tests:echo"})
    assert r2["ok"] is False


def test_plugin_failure_contains_and_closes_execution(runtime_host, metrics):
    r = runtime_host.handle({"cmd": "create_execution", "policy": "ephemeral", "ttl_seconds": 10})
    assert r["ok"] is True
    exec_id = r["execution"]

    h = runtime_host.handle({"cmd": "execution_plugin_get", "execution": exec_id, "plugin": "tests:fail"})["handle"]

    out = runtime_host.handle({"cmd": "execution_plugin_call", "execution": exec_id, "handle": h, "method": "boom"})
    assert out["ok"] is False, out
    # после падения execution должен быть закрыт/снесён (containment)
    still = runtime_host.handle({"cmd": "list_executions"})
    assert still["ok"] is True
    assert all(x["execution"] != exec_id for x in still["executions"])

    snap = runtime_host.handle({"cmd": "metrics_snapshot"})
    assert snap["ok"] is True
    # хотя бы один failed должен быть зафиксирован
    assert snap["metrics"]["executions"]["failed_total"] >= 1


def test_active_execution_limit_enforced(runtime_host):
    # runtime_host создан с max_active_executions=8 в фикстуре,
    # здесь делаем локально жёстче, чтобы реально проверить лимит.
    from invariant.ops.host import RuntimeHost
    from invariant.ops.metrics import RuntimeMetrics
    from invariant.ops.tracing import InMemoryRuntimeTracer
    from tests.fake_service import FakePluginService

    host = RuntimeHost(
        plugin_service=FakePluginService(tracer=InMemoryRuntimeTracer(metrics=RuntimeMetrics())),
        enable_watchdog=False,
        max_active_executions=2,
        default_ttl_seconds=60,
        cleanup_interval_seconds=60,
    )

    a = host.handle({"cmd": "create_execution", "policy": "ephemeral"})
    b = host.handle({"cmd": "create_execution", "policy": "ephemeral"})
    assert a["ok"] is True and b["ok"] is True

    c = host.handle({"cmd": "create_execution", "policy": "ephemeral"})
    assert c["ok"] is False, c


@pytest.mark.stress
def test_concurrent_plugin_calls_do_not_corrupt_state(runtime_host):
    r = runtime_host.handle({"cmd": "create_execution", "policy": "ephemeral", "ttl_seconds": 10})
    assert r["ok"] is True
    exec_id = r["execution"]
    h = runtime_host.handle({"cmd": "execution_plugin_get", "execution": exec_id, "plugin": "tests:sleep"})["handle"]

    def worker(i: int):
        # имитируем реальный конкурентный вызов на один execution
        out = runtime_host.handle(
            {"cmd": "execution_plugin_call", "execution": exec_id, "handle": h, "method": "sleep", "args": [0.03]}
        )
        return out

    n = 80
    with concurrent.futures.ThreadPoolExecutor(max_workers=24) as ex:
        results = list(ex.map(worker, range(n)))

    oks = sum(1 for x in results if x.get("ok") is True)
    fails = [x for x in results if x.get("ok") is not True]

    # В норме все должны быть ok (плагин не падает).
    # Если здесь фейлы — значит у вас гонки/закрытие сессии/handle-table corruption.
    assert oks == n, {"oks": oks, "fails_sample": fails[:3]}

    runtime_host.handle({"cmd": "execution_commit", "execution": exec_id})
