# tests/test_daemon_slowloris.py
from __future__ import annotations

import socket
import threading
import time

import pytest

from invariant.ops.daemon import InvariantHTTPServer, JsonAPIHandler
from tests.helpers_http import _read_http_response


@pytest.mark.soak
def test_slowloris_drip_feed_is_contained_by_concurrency_gate(runtime_host):
    # Create a dedicated server with small concurrency to make the test deterministic
    server = InvariantHTTPServer(
        ("127.0.0.1", 0),
        JsonAPIHandler,
        runtime_host=runtime_host,
        admin_token="secret",
        max_body_bytes=1024 * 1024,
        log_request_bodies=False,
        request_queue_size=128,
        socket_timeout_s=2.0,          # socket idle timeout
        max_concurrent_requests=4,     # easy to saturate
        acquire_timeout_s=0.01,
        retry_after_s=1,
    )

    t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()

    host, port = server.server_address

    slow_socks: list[socket.socket] = []
    stop = threading.Event()

    def drip_headers():
        # keep connections alive by sending header lines, never terminating headers with \r\n\r\n
        payload = b"X-Keep: 1\r\n"
        while not stop.is_set():
            for s in list(slow_socks):
                try:
                    s.sendall(payload)
                except Exception:
                    pass
            stop.wait(0.2)

    drip_thread = threading.Thread(target=drip_headers, daemon=True)
    drip_thread.start()

    try:
        # Open N slowloris connections to occupy all concurrency slots.
        for _ in range(4):
            s = socket.create_connection((host, port), timeout=2.0)
            s.settimeout(2.0)
            # Start a request but never finish headers:
            s.sendall(
                f"GET /v1/health HTTP/1.1\r\nHost: {host}:{port}\r\nUser-Agent: slowloris\r\n".encode("ascii")
            )
            slow_socks.append(s)

        # Give handler threads time to spawn & block on headers
        time.sleep(0.2)

        # With all slots occupied, the next request must be rejected quickly with 503 busy.
        s2 = socket.create_connection((host, port), timeout=2.0)
        s2.settimeout(2.0)
        s2.sendall(f"GET /v1/health HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode("ascii"))
        r = _read_http_response(s2, timeout=2.0)
        s2.close()

        assert r.status == 503, (r.status, r.body[:200])
        j = r.json()
        assert j.get("ok") is False and j.get("error") == "busy"

        # Free one slot
        slow_socks[0].close()
        slow_socks = slow_socks[1:]
        time.sleep(0.2)

        # Now health should succeed (200)
        s3 = socket.create_connection((host, port), timeout=2.0)
        s3.settimeout(2.0)
        s3.sendall(f"GET /v1/health HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode("ascii"))
        r2 = _read_http_response(s3, timeout=2.0)
        s3.close()

        assert r2.status == 200, (r2.status, r2.body[:200])
        assert r2.json().get("ok") is True

    finally:
        stop.set()
        for s in slow_socks:
            try:
                s.close()
            except Exception:
                pass

        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
        t.join(timeout=2.0)
