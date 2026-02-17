import time


def test_plugin_call_timeout_closes_execution(runtime_host):
    r = runtime_host.handle({"cmd": "create_execution", "policy": "ephemeral", "ttl_seconds": 10})
    assert r["ok"] is True
    exec_id = r["execution"]

    h = runtime_host.handle({"cmd": "execution_plugin_get", "execution": exec_id, "plugin": "tests:sleep"})["handle"]

    t0 = time.perf_counter()
    out = runtime_host.handle({
        "cmd": "execution_plugin_call",
        "execution": exec_id,
        "handle": h,
        "method": "sleep",
        "args": [0.2],
        "timeout_s": 0.05,
    })
    dt = time.perf_counter() - t0

    assert out["ok"] is False, out
    assert out["error"] == "timeout"
    assert dt < 0.2  # вернулись раньше фактического сна

    still = runtime_host.handle({"cmd": "list_executions"})
    assert still["ok"] is True
    assert all(x["execution"] != exec_id for x in still["executions"])
