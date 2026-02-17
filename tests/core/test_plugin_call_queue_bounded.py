import concurrent.futures as cf

import pytest

from invariant.ops.host import RuntimeHost


@pytest.fixture
def tight_host(fake_service):
    # workers=1, queue=1 => перегруз быстро даёт busy
    h = RuntimeHost(
        plugin_service=fake_service,
        enable_watchdog=False,
        plugin_call_workers=1,
        plugin_call_queue_size=1,
        default_plugin_call_timeout_s=2.0,
    )
    try:
        yield h
    finally:
        h.close()


def _create_exec(host, ttl=30):
    r = host.handle({"cmd": "create_execution", "policy": "ephemeral", "ttl_seconds": ttl})
    assert r["ok"] is True, r
    return r["execution"]


def _get_handle(host, exec_id, plugin_id):
    r = host.handle({"cmd": "execution_plugin_get", "execution": exec_id, "plugin": plugin_id})
    assert r["ok"] is True, r
    return r["handle"]


def test_busy_when_queue_full(tight_host):
    host = tight_host
    exec_id = _create_exec(host, ttl=30)
    h = _get_handle(host, exec_id, "tests:sleep")

    def one(_i: int):
        return host.handle({
            "cmd": "execution_plugin_call",
            "execution": exec_id,
            "handle": h,
            "method": "sleep",
            "args": [0.12],
            "timeout_s": 1.0,
        })

    n = 30
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        outs = list(ex.map(one, range(n)))

    oks = [o for o in outs if o.get("ok") is True]
    busies = [o for o in outs if o.get("ok") is False and o.get("error") == "busy"]

    assert len(oks) >= 1
    assert len(busies) >= 1, {"oks": len(oks), "sample": outs[:5]}

    host.handle({"cmd": "execution_commit", "execution": exec_id})
