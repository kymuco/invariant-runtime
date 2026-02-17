# tests/test_daemon_soak.py
from __future__ import annotations

import concurrent.futures
import os
import socket
import time

import pytest

from tests.helpers_http import http_json


def _soak_seconds() -> float:
    v = os.environ.get("INVARIANT_SOAK_SECONDS", "30").strip()
    try:
        s = float(v)
    except Exception:
        s = 30.0
    return max(10.0, s)


def _wait_port_closed(host: str, port: int, *, timeout_s: float = 2.5) -> None:
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                last = None
                time.sleep(0.05)
        except OSError:
            return
    raise AssertionError(f"port still open after {timeout_s}s (last={last})")


@pytest.mark.soak
def test_soak_traffic_steady_state_no_leaks(daemon_server):
    host, port = daemon_server.server_address
    secs = _soak_seconds()

    # Create a small pool of executions + handles (more realistic than single exec)
    exec_ids = []
    handles = []
    for _ in range(4):
        r = http_json(host, port, "POST", "/v1/handle", {"cmd": "create_execution", "policy": "ephemeral", "ttl_seconds": 60})
        assert r.status == 200, r.body[:200]
        eid = r.json()["execution"]
        exec_ids.append(eid)

        r2 = http_json(host, port, "POST", "/v1/handle", {"cmd": "execution_plugin_get", "execution": eid, "plugin": "tests:sleep"})
        assert r2.status == 200, r2.body[:200]
        handles.append(r2.json()["handle"])

    failures = []
    deadline = time.perf_counter() + secs

    def one_call(i: int):
        eid = exec_ids[i % len(exec_ids)]
        h = handles[i % len(handles)]
        return http_json(
            host,
            port,
            "POST",
            "/v1/handle",
            {"cmd": "execution_plugin_call", "execution": eid, "handle": h, "method": "sleep", "args": [0.01]},
            timeout=5.0,
        )

    # Keep load below max_concurrent_requests (fixture has 8); use 6 workers.
    max_workers = 6
    max_inflight = max_workers * 3

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        inflight = set()
        i = 0
        last_metrics_check = 0.0

        while time.perf_counter() < deadline:
            # top up inflight
            while len(inflight) < max_inflight:
                fut = ex.submit(one_call, i)
                inflight.add(fut)
                i += 1

            done, inflight = concurrent.futures.wait(inflight, timeout=0.2, return_when=concurrent.futures.FIRST_COMPLETED)

            for f in done:
                try:
                    resp = f.result()
                except Exception as e:
                    failures.append(repr(e))
                    continue

                if resp.status != 200:
                    failures.append(f"HTTP {resp.status}: {resp.body[:200]!r}")
                    continue

                j = resp.json()
                if not j.get("ok", False):
                    failures.append(f"ok=false: {j!r}")

            # Periodic metrics sanity while under load
            now = time.perf_counter()
            if now - last_metrics_check >= 1.0:
                last_metrics_check = now
                m = http_json(host, port, "GET", "/v1/metrics", timeout=3.0)
                assert m.status == 200, m.body[:200]
                snap = m.json()
                assert snap.get("ok") is True
                metrics = snap.get("metrics") or {}
                httpm = metrics.get("http") or {}
                execm = metrics.get("executions") or {}

                infl = int(httpm.get("inflight", 0))
                active = int(execm.get("active", 0))

                # under our load, inflight should stay bounded and non-negative
                assert infl >= 0
                assert infl <= 32  # generous upper bound

                # we created 4 executions and never commit during the loop
                assert active == 4

    assert not failures, f"failures({len(failures)}): {failures[:5]}"

    # Commit all executions
    for eid in exec_ids:
        r = http_json(host, port, "POST", "/v1/handle", {"cmd": "execution_commit", "execution": eid}, timeout=5.0)
        assert r.status == 200, r.body[:200]
        assert r.json().get("ok") is True

    # Ensure steady-state returns: active=0 and inflight=0 eventually
    deadline2 = time.time() + 2.5
    while time.time() < deadline2:
        m = http_json(host, port, "GET", "/v1/metrics", timeout=3.0)
        if m.status == 200:
            snap = m.json()
            metrics = (snap.get("metrics") or {})
            httpm = metrics.get("http") or {}
            execm = metrics.get("executions") or {}
            # NOTE: /v1/metrics request itself is counted as inflight at snapshot time.
            # So steady-state is inflight <= 1, not necessarily 0.
            if int(httpm.get("inflight", 0)) <= 1 and int(execm.get("active", 0)) == 0:
                return
        time.sleep(0.05)

    raise AssertionError("metrics did not return to steady-state (inflight=0, active=0) in time")


@pytest.mark.soak
def test_soak_shutdown_under_load(daemon_server):
    host, port = daemon_server.server_address

    # Prepare one execution to call during load
    r = http_json(host, port, "POST", "/v1/handle", {"cmd": "create_execution", "policy": "ephemeral", "ttl_seconds": 60})
    assert r.status == 200
    eid = r.json()["execution"]

    r2 = http_json(host, port, "POST", "/v1/handle", {"cmd": "execution_plugin_get", "execution": eid, "plugin": "tests:sleep"})
    assert r2.status == 200
    h = r2.json()["handle"]

    stop = False
    failures = []

    def spammer():
        nonlocal stop
        i = 0
        while not stop:
            try:
                resp = http_json(
                    host,
                    port,
                    "POST",
                    "/v1/handle",
                    {"cmd": "execution_plugin_call", "execution": eid, "handle": h, "method": "sleep", "args": [0.02]},
                    timeout=3.0,
                )
                # under normal operation should be 200; once shutdown begins, transport may fail — that's OK.
                if resp.status not in (200, 503):
                    failures.append(f"status={resp.status} body={resp.body[:120]!r}")
            except Exception:
                # during shutdown we expect connect/reset errors
                pass
            i += 1

    # Start background load
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(spammer) for _ in range(6)]
        time.sleep(0.8)

        # Request shutdown (must respond quickly)
        s = http_json(host, port, "POST", "/v1/shutdown", {}, headers={"Authorization": "Bearer secret"}, timeout=3.0)
        assert s.status == 200, s.body[:200]
        assert s.json().get("ok") is True

        # Stop spammers shortly after shutdown request
        time.sleep(0.3)
        stop = True
        concurrent.futures.wait(futs, timeout=3.0)

    # The port should stop accepting connections in bounded time
    _wait_port_closed(host, port, timeout_s=3.0)

    # We don't enforce "no failures" here because shutdown transitions can cause 503/transport errors.
    # But we do enforce that there were no bizarre statuses.
    assert not failures, f"unexpected statuses: {failures[:5]}"
