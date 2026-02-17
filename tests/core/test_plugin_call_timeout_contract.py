import time


def _create_exec(host, ttl=20):
    r = host.handle({"cmd": "create_execution", "policy": "ephemeral", "ttl_seconds": ttl})
    assert r["ok"] is True, r
    return r["execution"]


def _get_handle(host, exec_id, plugin_id):
    r = host.handle({"cmd": "execution_plugin_get", "execution": exec_id, "plugin": plugin_id})
    assert r["ok"] is True, r
    return r["handle"]


def _list_exec_ids(host):
    r = host.handle({"cmd": "list_executions"})
    assert r["ok"] is True, r
    return [x["execution"] for x in r["executions"]]


def test_timeout_returns_fast_and_closes_execution(runtime_host):
    host = runtime_host

    exec_id = _create_exec(host, ttl=30)
    h = _get_handle(host, exec_id, "tests:sleep")

    sleep_s = 0.50
    timeout_s = 0.05

    t0 = time.perf_counter()
    out = host.handle({
        "cmd": "execution_plugin_call",
        "execution": exec_id,
        "handle": h,
        "method": "sleep",
        "args": [sleep_s],
        "timeout_s": timeout_s,
    })
    dt = time.perf_counter() - t0

    assert out["ok"] is False, out
    assert out["error"] == "timeout", out

    # Windows может дать дрожание таймеров; проверяем "существенно раньше сна"
    assert dt < 0.25, f"returned too late: dt={dt:.6f}s (sleep_s={sleep_s})"

    # containment: execution должен исчезнуть
    assert exec_id not in _list_exec_ids(host)
