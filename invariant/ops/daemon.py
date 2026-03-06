# invariant/ops/daemon.py
from __future__ import annotations

import argparse
import hmac
import importlib
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Mapping
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from invariant.ops.host import RuntimeHost

log = logging.getLogger("invariant.daemon")


# ----------------------------
# Factory loading
# ----------------------------

def _parse_factory(spec: str) -> Callable[[], Any]:
    """
    spec: "module.submodule:function_name"
    returns callable that builds PluginService
    """
    if ":" not in spec:
        raise ValueError("Factory spec must be like 'module:function'")
    mod_name, fn_name = spec.split(":", 1)
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, fn_name, None)
    if fn is None or not callable(fn):
        raise ValueError(f"Factory function not found/callable: {spec}")
    return fn


def _default_factory() -> Any:
    """
    Default tries:
      - invariant.app:build_service
      - app:build_service
    """
    for spec in ("invariant.app:build_service", "app:build_service"):
        try:
            return _parse_factory(spec)()
        except Exception:
            continue
    raise RuntimeError(
        "No factory found. Provide --factory module:function or set INVARIANT_FACTORY."
    )

def _json_default(o):
    if isinstance(o, Enum):
        return o.name
    if isinstance(o, Path):
        return str(o)
    return str(o)  # last resort


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(str(name), "")
    if not isinstance(raw, str) or not raw.strip():
        return bool(default)
    val = raw.strip().lower()
    if val in {"1", "true", "yes", "y", "on"}:
        return True
    if val in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


# ----------------------------
# HTTP server with shared state
# ----------------------------

class InvariantHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass,
        *,
        runtime_host: RuntimeHost,
        admin_token: str | None,
        api_token: str | None = None,
        max_body_bytes: int,
        log_request_bodies: bool,
        request_queue_size: int = 128,
        socket_timeout_s: float = 30.0,
        max_concurrent_requests: int = 64,
        acquire_timeout_s: float = 0.25,
        retry_after_s: int = 1,
        per_client_rps: float = 0.0,
        per_client_burst: int = 0,
        run_worker_enabled: bool = False,
        run_worker_id: str | None = None,
        run_worker_lease_seconds: int = 120,
        run_worker_poll_interval_s: float = 0.5,
        run_worker_reclaim_expired: bool = True,
        run_alerting_enabled: bool = False,
        run_alerting_poll_interval_s: float = 30.0,
        run_alerting_webhook_url: str | None = None,
        run_alerting_cooldown_s: int = 300,
        run_replay_sampler_enabled: bool = False,
        run_replay_sampler_poll_interval_s: float = 60.0,
        run_replay_sampler_scan_limit: int = 200,
        run_replay_sampler_batch_size: int = 20,
        run_replay_sampler_cooldown_s: float = 900.0,
        run_replay_sampler_strict: bool = True,
    ):
        # IMPORTANT: must be set BEFORE super().__init__ because server_activate() calls listen(backlog)
        self.request_queue_size = max(16, int(request_queue_size))
        super().__init__(server_address, RequestHandlerClass)
        self.runtime_host = runtime_host
        self.admin_token = admin_token
        self.api_token = (api_token.strip() if isinstance(api_token, str) and api_token.strip() else None)
        self.max_body_bytes = int(max_body_bytes)
        self.log_request_bodies = bool(log_request_bodies)
        self.socket_timeout_s = float(socket_timeout_s)
        self.boot_id = str(uuid.uuid4())
        self.started_at = time.time()

        mcr = int(max_concurrent_requests)
        if mcr <= 0:
            raise ValueError("max_concurrent_requests must be > 0")
        self.max_concurrent_requests = mcr
        self.acquire_timeout_s = max(0.0, float(acquire_timeout_s))
        self.retry_after_s = max(0, int(retry_after_s))
        self._sem = threading.BoundedSemaphore(value=self.max_concurrent_requests)

        # ---- per-client rate limit (token bucket) ----
        self.per_client_rps = max(0.0, float(per_client_rps))
        self.per_client_burst = max(0, int(per_client_burst))
        self._rl_lock = threading.Lock()
        self._rl: dict[str, tuple[float, float]] = {}  # ip -> (tokens, last_ts)
        self._rl_max_entries = 4096
        self._rl_ttl_s = 600.0
        self.run_worker_enabled = bool(run_worker_enabled)
        self.run_worker_id = str(run_worker_id or "").strip()
        self.run_worker_lease_seconds = max(1, int(run_worker_lease_seconds))
        self.run_worker_poll_interval_s = max(0.05, float(run_worker_poll_interval_s))
        self.run_worker_reclaim_expired = bool(run_worker_reclaim_expired)
        self.run_alerting_enabled = bool(run_alerting_enabled)
        self.run_alerting_poll_interval_s = max(1.0, float(run_alerting_poll_interval_s))
        self.run_alerting_webhook_url = str(run_alerting_webhook_url or "").strip()
        self.run_alerting_cooldown_s = max(0, int(run_alerting_cooldown_s))
        self.run_replay_sampler_enabled = bool(run_replay_sampler_enabled)
        self.run_replay_sampler_poll_interval_s = max(1.0, float(run_replay_sampler_poll_interval_s))
        self.run_replay_sampler_scan_limit = max(1, int(run_replay_sampler_scan_limit))
        self.run_replay_sampler_batch_size = max(1, int(run_replay_sampler_batch_size))
        self.run_replay_sampler_cooldown_s = max(0.0, float(run_replay_sampler_cooldown_s))
        self.run_replay_sampler_strict = bool(run_replay_sampler_strict)

    def handle_error(self, request, client_address):
        """
        socketserver prints noisy tracebacks by default.
        In prod and under load tests, client disconnects are normal.
        We suppress expected network errors and log unexpected ones.
        """
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        # Windows often wraps disconnects as OSError 10053/10054
        if isinstance(exc, OSError) and getattr(exc, "winerror", None) in (10053, 10054):
            return
        log.exception("Unhandled server error from %s", client_address)

    def _drain_socket_best_effort(self, request, *, max_bytes: int = 16384, timeout_s: float = 0.02) -> None:
        """
        Best-effort drain to reduce Windows RST/10054 when we close early.
        We only try to consume enough to cover request headers.
        """
        try:
            request.settimeout(timeout_s)
            remaining = int(max_bytes)
            seen = b""
            while remaining > 0:
                chunk = request.recv(min(4096, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                seen += chunk
                if b"\r\n\r\n" in seen:
                    break
        except Exception:
            pass

    def release(self) -> None:
        try:
            self._sem.release()
        except ValueError:
            # Over-release should never happen; if it does, we contain.
            pass

    def _send_busy_response(self, request) -> None:
        """
        Called from accept loop thread (before handler thread is created).
        We cannot rely on request headers here, so we generate request_id.
        """
        try:
            # Reduce chances of WinError 10054 on clients under load
            self._drain_socket_best_effort(request)
            req_id = str(uuid.uuid4())
            payload = {
                "ok": False,
                "error": "busy",
                "message": "Server is overloaded. Try again.",
                "request_id": req_id,
            }
            body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")

            headers = [
                b"HTTP/1.1 503 Service Unavailable\r\n",
                b"Content-Type: application/json; charset=utf-8\r\n",
                f"Content-Length: {len(body)}\r\n".encode(),
                b"Connection: close\r\n",
                f"X-Request-Id: {req_id}\r\n".encode(),
            ]
            if self.retry_after_s:
                headers.append(f"Retry-After: {self.retry_after_s}\r\n".encode())
            headers.append(b"\r\n")

            request.sendall(b"".join(headers) + body)
        except Exception:
            # containment: refusal path must not crash accept loop
            pass
        finally:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                request.close()
            except Exception:
                pass

    def _send_rate_limited_response(self, request, *, retry_after_s: int = 1) -> None:
        try:
            self._drain_socket_best_effort(request)
            req_id = str(uuid.uuid4())
            payload = {
                "ok": False,
                "error": "rate_limited",
                "message": "Too many requests. Slow down.",
                "request_id": req_id,
            }
            body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
            headers = [
                b"HTTP/1.1 429 Too Many Requests\r\n",
                b"Content-Type: application/json; charset=utf-8\r\n",
                f"Content-Length: {len(body)}\r\n".encode(),
                b"Connection: close\r\n",
                f"X-Request-Id: {req_id}\r\n".encode(),
                f"Retry-After: {int(retry_after_s)}\r\n".encode(),
                b"\r\n",
            ]
            request.sendall(b"".join(headers) + body)
        except Exception:
            pass
        finally:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                request.close()
            except Exception:
                pass

    def _rate_limit_allow(self, ip: str) -> bool:
        if self.per_client_rps <= 0.0 or self.per_client_burst <= 0:
            return True
        now = time.time()
        with self._rl_lock:
            # Bound memory: periodic pruning.
            if len(self._rl) > 10000:
                cutoff = now - 600.0  # 10 min
                for k, (_tok, last_ts) in list(self._rl.items()):
                    if last_ts < cutoff:
                        self._rl.pop(k, None)
                # If still too big, fail closed-ish: drop state (rate-limit becomes permissive temporarily)
                if len(self._rl) > 20000:
                    self._rl.clear()
            # cleanup old entries (bounded work)
            if len(self._rl) > self._rl_max_entries:
                # drop oldest by last_ts
                oldest = sorted(self._rl.items(), key=lambda kv: kv[1][1])[: max(1, len(self._rl) - self._rl_max_entries)]
                for k, _ in oldest:
                    self._rl.pop(k, None)
            else:
                # opportunistic TTL cleanup
                cutoff = now - self._rl_ttl_s
                # keep it bounded: remove at most 128 per call
                removed = 0
                for k, (_t, last) in list(self._rl.items()):
                    if last < cutoff:
                        self._rl.pop(k, None)
                        removed += 1
                        if removed >= 128:
                            break
            tokens, last = self._rl.get(ip, (float(self.per_client_burst), now))
            # refill
            tokens = min(float(self.per_client_burst), tokens + (now - last) * float(self.per_client_rps))
            if tokens < 1.0:
                self._rl[ip] = (tokens, now)
                return False
            self._rl[ip] = (tokens - 1.0, now)
            return True

    def process_request(self, request, client_address) -> None:
        """
        Concurrency gate BEFORE spawning a new thread.
        This prevents unbounded thread creation under load.
        """
        # rate limit BEFORE spawning thread
        ip = (client_address[0] if client_address else "") or ""
        if ip and not self._rate_limit_allow(ip):
            self._send_rate_limited_response(request, retry_after_s=self.retry_after_s or 1)
            return

        acquired = False
        try:
            acquired = self._sem.acquire(timeout=self.acquire_timeout_s)
        except Exception:
            acquired = False

        if not acquired:
            self._send_busy_response(request)
            return

        try:
            super().process_request(request, client_address)
        except Exception:
            # If thread creation/dispatch fails, release immediately.
            self.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.release()


# ----------------------------
# Request handler
# ----------------------------

class JsonAPIHandler(BaseHTTPRequestHandler):
    """
    Endpoints:
      GET  /v1/health
      GET  /v1/metrics
      GET  /v1/executions
      POST /v1/handle          (body = {"cmd": ...})
      POST /v1/shutdown        (admin token required if configured)

    Notes:
      - Handler reads shared objects from self.server.* (reliable),
        NOT from attributes set after construction (which can race).
    """

    server: InvariantHTTPServer  # for type hints
    server_version = "InvariantDaemon/0.2"
    protocol_version = "HTTP/1.1"

    _last_status: int | None = None

    def handle_expect_100(self) -> bool:
        """
        Support Expect: 100-continue so clients don't send huge bodies
        that we will reject anyway. This dramatically reduces WinError 10053.
        """
        try:
            expect = (self.headers.get("Expect") or "").strip().lower()
            if expect != "100-continue":
                return True

            length = int(self.headers.get("Content-Length", "0") or "0")
            if length > self.server.max_body_bytes:
                req_id = self._get_req_id()
                self._send_json(
                    413,
                    {
                        "ok": False,
                        "error": "payload_too_large",
                        "message": f"Body too large: {length} > {self.server.max_body_bytes}",
                    },
                    req_id=req_id,
                )
                return False

            # accept body
            self.send_response_only(100)
            self.end_headers()
            return True
        except Exception:
            # If anything goes wrong, don't block request; fall back to normal flow.
            return True

    def setup(self) -> None:
        super().setup()
        try:
            self.connection.settimeout(self.server.socket_timeout_s)
        except Exception:
            pass

    # --- logging ---

    def log_message(self, fmt: str, *args: Any) -> None:
        # route BaseHTTPRequestHandler default logs into logging
        log.info("%s - %s", self.address_string(), fmt % args)

    def _now(self) -> float:
        return time.time()

    def _get_req_id(self) -> str:
        rid = self.headers.get("X-Request-Id")
        return rid.strip() if rid and rid.strip() else str(uuid.uuid4())
    
    def _client_disconnected(self, e: BaseException) -> bool:
        if isinstance(e, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return True
        if isinstance(e, OSError) and getattr(e, "winerror", None) in (10053, 10054):
            return True
        return False

    def _send_json(self, code: int, obj: Any, *, req_id: str | None = None, headers: dict[str, str] | None = None) -> None:
        payload = obj
        if isinstance(payload, dict) and req_id:
            payload = dict(payload)
            payload.setdefault("request_id", req_id)

        data = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")

        # allow metrics to see last response code
        try:
            self._last_status = int(code)
        except Exception:
            pass

        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        if req_id:
            self.send_header("X-Request-Id", req_id)
        if headers:
            for k, v in headers.items():
                if k and v is not None:
                    self.send_header(str(k), str(v))
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception as e:
            # client disconnected mid-response; do not escalate
            if self._client_disconnected(e):
                return
            # client disconnected; nothing to do
            raise
        self.close_connection = True

    def _read_raw_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return b""
        if length > self.server.max_body_bytes:
            raise ValueError(f"Body too large: {length} > {self.server.max_body_bytes}")
        return self._read_exactly(length)

    def _read_exactly(self, n: int) -> bytes:
        """
        Read exactly n bytes (or raise on unexpected EOF).
        """
        buf = bytearray()
        while len(buf) < n:
            chunk = self.rfile.read(n - len(buf))
            if not chunk:
                raise ValueError("Unexpected EOF while reading request body")
            buf.extend(chunk)
        return bytes(buf)

    def _read_json(self) -> dict[str, Any]:
        raw = self._read_raw_body()
        if not raw:
            return {}
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception as e:
            raise ValueError(f"Invalid JSON: {e}") from e
        if not isinstance(obj, dict):
            raise ValueError("JSON body must be an object")
        return obj

    def _require_admin(self) -> bool:
        tok = self.server.admin_token
        if not tok:
            return False
        auth = (self.headers.get("Authorization", "") or "").strip()
        return hmac.compare_digest(auth, f"Bearer {tok}")
    
    def _require_api(self) -> bool:
        tok = getattr(self.server, "api_token", None)
        if not tok:
            return True
        auth = (self.headers.get("Authorization", "") or "").strip()
        return hmac.compare_digest(auth, f"Bearer {tok}")

    def _log_exception(self, *, req_id: str, where: str, exc: Exception, body: bytes | None = None) -> None:
        # Full traceback into logs, but keep client response minimal.
        tb = traceback.format_exc()
        msg = {
            "request_id": req_id,
            "where": where,
            "path": getattr(self, "path", None),
            "method": getattr(self, "command", None),
            "error": f"{type(exc).__name__}: {exc}",
        }
        if self.server.log_request_bodies and body:
            # Keep it bounded to avoid log bombs
            msg["body_preview"] = body[:4096].decode("utf-8", errors="replace")
        log.error("Unhandled exception: %s\n%s", msg, tb)

    def _route_label(self) -> str:
        # stable label for metrics; strip query string
        p = (getattr(self, "path", "") or "").split("?", 1)[0]
        if p in ("/", "/v1/health", "/v1/metrics", "/v1/executions", "/v1/handle", "/v1/shutdown"):
            return p
        # keep it bounded
        if p.startswith("/v1/"):
            return "/v1/other"
        return "/other"

    # --- request dispatch wrapper (adds duration + request id) ---

    def _dispatch(self, fn: Callable[[str], None]) -> None:
        req_id = self._get_req_id()
        t0 = time.perf_counter()
        raw_body: bytes | None = None
        self._last_status = None

        metrics = None
        try:
            metrics = self.server.runtime_host.metrics()
        except Exception:
            metrics = None
        if metrics:
            try:
                metrics.http_requests_total.inc(1)
            except Exception:
                pass

        if metrics:
            try:
                metrics.http_inflight.inc(1)
            except Exception:
                pass

        try:
            # If it's a POST, read body once here (so we can log on failure)
            if self.command == "POST":
                raw_body = self._read_raw_body()
                # Put it back for later parsing
                self._raw_body_cache = raw_body
            fn(req_id)
        except ValueError as e:
            self._send_json(
                400,
                {"ok": False, "error": "bad_request", "message": str(e)},
                req_id=req_id,
            )
        except Exception as e:
            # If the client is gone, don't try to write a response or log as server error.
            if self._client_disconnected(e):
                return
            self._log_exception(req_id=req_id, where="dispatch", exc=e, body=raw_body)
            # Best-effort 500. If client disconnects here, just stop.
            try:
                self._send_json(
                    500,
                    {"ok": False, "error": "internal_error", "message": str(e)},
                    req_id=req_id,
                )
            except Exception as e2:
                if self._client_disconnected(e2):
                    return
                raise
        finally:
            dt = time.perf_counter() - t0
            if metrics:
                try:
                    metrics.http_inflight.dec(1)
                except Exception:
                    pass

            if metrics:
                try:
                    route = self._route_label()
                    label = f"{self.command} {route}"
                    metrics.http_request_duration_seconds.observe(label, dt)
                    status = getattr(self, "_last_status", None)
                    if status is not None:
                        metrics.http_responses_total_by_status.inc(str(status), 1)
                except Exception:
                    pass
            log.info(
                "req done",
                extra={
                    "request_id": req_id,
                    "method": self.command,
                    "path": self.path,
                    "dt_ms": round(dt * 1000, 3),
                    "client": self.address_string(),
                },
            )

    # --- cached body parsing (because we pre-read in _dispatch) ---

    def _read_json_cached(self) -> dict[str, Any]:
        raw = getattr(self, "_raw_body_cache", b"")
        if not raw:
            return {}
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception as e:
            raise ValueError(f"Invalid JSON: {e}") from e
        if not isinstance(obj, dict):
            raise ValueError("JSON body must be an object")
        return obj

    # --- handlers ---

    def do_GET(self) -> None:
        self._dispatch(self._do_GET_impl)

    def _do_GET_impl(self, req_id: str) -> None:
        # If api_token is configured: everything except health requires auth
        if getattr(self.server, "api_token", None) and self.path not in ("/", "/v1/health"):
            if not self._require_api():
                self._send_json(
                    403,
                    {"ok": False, "error": "forbidden", "message": "bad token"},
                    req_id=req_id,
                )
                return

        if self.path == "/":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "invariant-daemon",
                    "boot_id": self.server.boot_id,
                    "started_at": self.server.started_at,
                    "endpoints": [
                        "GET /v1/health",
                        "GET /v1/metrics",
                        "GET /v1/executions",
                        "POST /v1/handle",
                        "POST /v1/shutdown",
                    ],
                },
                req_id=req_id,
            )
            return

        if self.path == "/v1/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "ts": self._now(),
                    "boot_id": self.server.boot_id,
                    "started_at": self.server.started_at,
                    "agent_run_worker": {
                        "enabled": bool(getattr(self.server, "run_worker_enabled", False)),
                        "worker_id": str(getattr(self.server, "run_worker_id", "") or ""),
                        "lease_seconds": int(getattr(self.server, "run_worker_lease_seconds", 120)),
                        "poll_interval_s": float(getattr(self.server, "run_worker_poll_interval_s", 0.5)),
                        "reclaim_expired": bool(getattr(self.server, "run_worker_reclaim_expired", True)),
                    },
                    "agent_run_alerting": {
                        "enabled": bool(getattr(self.server, "run_alerting_enabled", False)),
                        "poll_interval_s": float(getattr(self.server, "run_alerting_poll_interval_s", 30.0)),
                        "cooldown_s": int(getattr(self.server, "run_alerting_cooldown_s", 300)),
                        "webhook_configured": bool(str(getattr(self.server, "run_alerting_webhook_url", "") or "").strip()),
                    },
                    "agent_run_replay_sampler": {
                        "enabled": bool(getattr(self.server, "run_replay_sampler_enabled", False)),
                        "poll_interval_s": float(getattr(self.server, "run_replay_sampler_poll_interval_s", 60.0)),
                        "scan_limit": int(getattr(self.server, "run_replay_sampler_scan_limit", 200)),
                        "batch_size": int(getattr(self.server, "run_replay_sampler_batch_size", 20)),
                        "cooldown_seconds": float(getattr(self.server, "run_replay_sampler_cooldown_s", 900.0)),
                        "strict": bool(getattr(self.server, "run_replay_sampler_strict", True)),
                    },
                },
                req_id=req_id,
            )
            return

        if self.path == "/v1/metrics":
            resp = self.server.runtime_host.handle({"cmd": "metrics_snapshot"})
            self._send_json(200, resp, req_id=req_id)
            return

        if self.path == "/v1/executions":
            resp = self.server.runtime_host.handle({"cmd": "list_executions"})
            self._send_json(200, resp, req_id=req_id)
            return

        self._send_json(
            404,
            {"ok": False, "error": "not_found", "message": self.path},
            req_id=req_id,
        )

    def do_POST(self) -> None:
        self._dispatch(self._do_POST_impl)

    def _do_POST_impl(self, req_id: str) -> None:

        if self.path == "/v1/handle":
            if not self._require_api():
                self._send_json(
                    403,
                    {"ok": False, "error": "forbidden", "message": "bad token"},
                    req_id=req_id,
                )
                return
            ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if ctype and ctype != "application/json":
                raise ValueError("Content-Type must be application/json")
            cmd = self._read_json_cached()
            if not isinstance(cmd, dict):
                raise ValueError("Command must be a JSON object")

            # Optional: cheap guardrail (prevents crashing host with huge nested)
            if "cmd" not in cmd:
                raise ValueError("Missing 'cmd'")

            resp = self.server.runtime_host.handle(cmd)
            self._send_json(200, resp, req_id=req_id)
            return

        if self.path == "/v1/shutdown":
            # Auth policy:
            # 1) admin_token configured -> require admin token
            # 2) no admin_token but api_token configured -> require api token
            # 3) no tokens configured -> allow local shutdown
            has_admin = bool(getattr(self.server, "admin_token", None))
            has_api = bool(getattr(self.server, "api_token", None))
            if has_admin and (not self._require_admin()):
                self._send_json(
                    403,
                    {"ok": False, "error": "forbidden", "message": "bad admin token"},
                    req_id=req_id,
                )
                return
            if (not has_admin) and has_api and (not self._require_api()):
                self._send_json(
                    403,
                    {"ok": False, "error": "forbidden", "message": "bad token"},
                    req_id=req_id,
                )
                return

            # Respond first, then shutdown asynchronously (avoid deadlocks in handler thread)
            self._send_json(200, {"ok": True}, req_id=req_id)
            def _shutdown_and_close() -> None:
                try:
                    self.server.shutdown()
                finally:
                    # IMPORTANT: shutdown() stops serve_forever but does not close the listening socket.
                    # In a long-lived process (like tests), we must close it explicitly.
                    try:
                        self.server.server_close()
                    except Exception:
                        pass

            threading.Thread(target=_shutdown_and_close, daemon=True).start()
            return

        self._send_json(
            404,
            {"ok": False, "error": "not_found", "message": self.path},
            req_id=req_id,
        )


# ----------------------------
# Daemon runner
# ----------------------------

def _setup_logging(level: str) -> None:
    lvl = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        stream=sys.stdout,
    )


def _agent_run_queue_worker_loop(
    *,
    runtime_host: RuntimeHost,
    stop_event: threading.Event,
    worker_id: str,
    lease_seconds: int,
    poll_interval_s: float,
    reclaim_expired: bool,
) -> None:
    wid = str(worker_id).strip() or f"daemon-worker-{uuid.uuid4().hex[:8]}"
    lease = max(1, int(lease_seconds))
    poll_s = max(0.05, float(poll_interval_s))
    claimed_jobs = 0
    idle_loops = 0
    failures = 0

    log.info(
        "Agent run queue worker started worker_id=%s lease_seconds=%s poll_interval_s=%.3f reclaim_expired=%s",
        wid,
        lease,
        poll_s,
        bool(reclaim_expired),
    )

    while not stop_event.is_set():
        try:
            resp = runtime_host.handle(
                {
                    "cmd": "agent_run_worker_process",
                    "worker_id": wid,
                    "lease_seconds": lease,
                    "reclaim_expired": bool(reclaim_expired),
                }
            )
        except Exception:
            failures += 1
            log.exception("Agent run queue worker crashed on runtime_host.handle")
            stop_event.wait(min(2.0, poll_s))
            continue

        if not isinstance(resp, Mapping):
            failures += 1
            if failures <= 3 or failures % 50 == 0:
                log.warning("Agent run queue worker received non-object response: %r", resp)
            stop_event.wait(min(2.0, poll_s))
            continue

        if not bool(resp.get("ok", False)):
            err = str(resp.get("error") or "")
            msg = str(resp.get("message") or "")
            if err == "invalid_command":
                log.warning("Agent run queue worker disabled: %s", msg or err)
                break
            failures += 1
            if failures <= 3 or failures % 50 == 0:
                log.warning("Agent run queue worker call failed error=%s message=%s", err or "unknown", msg)
            stop_event.wait(min(2.0, poll_s))
            continue

        worker = resp.get("worker")
        if not isinstance(worker, Mapping):
            failures += 1
            if failures <= 3 or failures % 50 == 0:
                log.warning("Agent run queue worker missing 'worker' payload: %r", resp)
            stop_event.wait(min(2.0, poll_s))
            continue

        if not bool(worker.get("ok", False)):
            failures += 1
            if failures <= 3 or failures % 50 == 0:
                log.warning("Agent run queue worker returned worker.ok=false: %r", worker)
            stop_event.wait(min(2.0, poll_s))
            continue

        if bool(worker.get("claimed", False)):
            claimed_jobs += 1
            idle_loops = 0
            if claimed_jobs <= 3 or claimed_jobs % 100 == 0:
                log.info("Agent run queue worker processed jobs=%s worker_id=%s", claimed_jobs, wid)
            continue

        idle_loops += 1
        stop_event.wait(poll_s)

    log.info(
        "Agent run queue worker stopped worker_id=%s claimed_jobs=%s idle_loops=%s failures=%s",
        wid,
        claimed_jobs,
        idle_loops,
        failures,
    )


def _coerce_float(raw: Any, *, default: float, minimum: float | None = None) -> float:
    try:
        out = float(raw)
    except Exception:
        out = float(default)
    if minimum is not None:
        out = max(float(minimum), out)
    return out


def _coerce_int(raw: Any, *, default: int, minimum: int | None = None) -> int:
    try:
        out = int(raw)
    except Exception:
        out = int(default)
    if minimum is not None:
        out = max(int(minimum), out)
    return out


def _coerce_bool(raw: Any, *, default: bool = False) -> bool:
    if raw is None:
        return bool(default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
    return bool(default)


def _resolve_alerting_profile_name(policy_profile: str | None) -> str:
    if isinstance(policy_profile, str) and policy_profile.strip():
        return str(policy_profile).strip()
    raw = str(os.environ.get("INVARIANT_POLICY_PROFILE", "")).strip()
    return raw or "dev"


def _resolve_agent_run_alerting_config(*, service: Any, policy_profile: str | None) -> dict[str, Any]:
    profile = _resolve_alerting_profile_name(policy_profile)
    raw_cfg: dict[str, Any] = {}
    provider = getattr(service, "policy_config_for_profile", None)
    if callable(provider):
        try:
            loaded = provider(profile)
            if isinstance(loaded, Mapping):
                raw_cfg = dict(loaded)
        except Exception:
            log.exception("Failed to load alerting policy config profile=%s", profile)

    enabled = _coerce_bool(raw_cfg.get("run_factory.alerts.enabled"), default=False)
    webhook_url = str(raw_cfg.get("run_factory.alerts.webhook_url") or "").strip()
    timeout_s = _coerce_float(raw_cfg.get("run_factory.alerts.timeout_s"), default=5.0, minimum=0.1)
    cooldown_s = _coerce_int(raw_cfg.get("run_factory.alerts.cooldown_s"), default=300, minimum=0)
    poll_interval_s = _coerce_float(raw_cfg.get("run_factory.alerts.poll_interval_s"), default=30.0, minimum=1.0)
    scan_limit = _coerce_int(raw_cfg.get("run_factory.alerts.scan_limit"), default=1000, minimum=1)
    fail_spike_window = _coerce_int(raw_cfg.get("run_factory.alerts.fail_spike_window"), default=20, minimum=1)
    fail_spike_threshold_pct = _coerce_float(
        raw_cfg.get("run_factory.alerts.fail_spike_threshold_pct"),
        default=40.0,
        minimum=0.0,
    )
    stuck_run_seconds = _coerce_int(raw_cfg.get("run_factory.alerts.stuck_run_seconds"), default=1800, minimum=1)
    replay_target_pct = _coerce_float(raw_cfg.get("run_factory.alerts.replay_target_pct"), default=95.0, minimum=0.0)
    trace_target_pct = _coerce_float(raw_cfg.get("run_factory.alerts.trace_target_pct"), default=100.0, minimum=0.0)

    state_path_raw = str(raw_cfg.get("run_factory.alerts.state_path") or "").strip()
    if not state_path_raw:
        state_path_raw = ".invariant/run_registry/run_factory/alerts_state.json"
    state_path = Path(state_path_raw)
    if not state_path.is_absolute():
        state_path = state_path.resolve()

    secret_env_name = str(raw_cfg.get("run_factory.alerts.webhook_hmac_secret_env") or "").strip()
    hmac_secret: str | None = None
    if secret_env_name:
        hmac_secret = str(os.environ.get(secret_env_name, "")).strip() or None

    return {
        "profile": profile,
        "enabled": bool(enabled and webhook_url),
        "configured_enabled": bool(enabled),
        "webhook_url": webhook_url,
        "timeout_s": float(timeout_s),
        "cooldown_s": int(cooldown_s),
        "poll_interval_s": float(poll_interval_s),
        "scan_limit": int(scan_limit),
        "fail_spike_window": int(fail_spike_window),
        "fail_spike_threshold_pct": float(fail_spike_threshold_pct),
        "stuck_run_seconds": int(stuck_run_seconds),
        "replay_target_pct": float(replay_target_pct),
        "trace_target_pct": float(trace_target_pct),
        "state_path": str(state_path),
        "hmac_secret": hmac_secret,
    }


def _resolve_agent_run_replay_sampler_config(*, service: Any, policy_profile: str | None) -> dict[str, Any]:
    profile = _resolve_alerting_profile_name(policy_profile)
    raw_cfg: dict[str, Any] = {}
    provider = getattr(service, "policy_config_for_profile", None)
    if callable(provider):
        try:
            loaded = provider(profile)
            if isinstance(loaded, Mapping):
                raw_cfg = dict(loaded)
        except Exception:
            log.exception("Failed to load replay sampler policy config profile=%s", profile)

    enabled = _coerce_bool(raw_cfg.get("run_factory.replay_sampler.enabled"), default=False)
    poll_interval_s = _coerce_float(raw_cfg.get("run_factory.replay_sampler.poll_interval_s"), default=60.0, minimum=1.0)
    scan_limit = _coerce_int(raw_cfg.get("run_factory.replay_sampler.scan_limit"), default=200, minimum=1)
    max_runs = _coerce_int(raw_cfg.get("run_factory.replay_sampler.max_runs"), default=20, minimum=1)
    cooldown_s = _coerce_float(raw_cfg.get("run_factory.replay_sampler.cooldown_seconds"), default=900.0, minimum=0.0)
    strict = _coerce_bool(raw_cfg.get("run_factory.replay_sampler.strict"), default=True)
    policy = str(raw_cfg.get("run_factory.replay_sampler.policy") or "ephemeral").strip().lower()
    if policy not in {"ephemeral", "managed"}:
        policy = "ephemeral"
    ttl_seconds = _coerce_int(raw_cfg.get("run_factory.replay_sampler.ttl_seconds"), default=1200, minimum=1)

    return {
        "profile": profile,
        "enabled": bool(enabled),
        "configured_enabled": bool(enabled),
        "poll_interval_s": float(poll_interval_s),
        "scan_limit": int(scan_limit),
        "max_runs": int(max_runs),
        "cooldown_seconds": float(cooldown_s),
        "strict": bool(strict),
        "policy": policy,
        "ttl_seconds": int(ttl_seconds),
    }


def _read_alert_state(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {"alerts": {}}
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            return {"alerts": {}}
        alerts = raw.get("alerts")
        if not isinstance(alerts, Mapping):
            return {"alerts": {}}
        return {
            "alerts": {str(k): dict(v) for k, v in alerts.items() if isinstance(v, Mapping)},
        }
    except Exception:
        return {"alerts": {}}


def _write_alert_state(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"alerts": dict(data.get("alerts") or {}) if isinstance(data.get("alerts"), Mapping) else {}}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _alert_fingerprint(alert: Mapping[str, Any]) -> str:
    material = {
        "id": str(alert.get("id") or ""),
        "severity": str(alert.get("severity") or ""),
        "value_pct": alert.get("value_pct"),
        "value": alert.get("value"),
        "threshold_pct": alert.get("threshold_pct"),
        "threshold_seconds": alert.get("threshold_seconds"),
    }
    return json.dumps(material, ensure_ascii=False, sort_keys=True)


def _send_alert_webhook(
    *,
    webhook_url: str,
    payload: Mapping[str, Any],
    timeout_s: float,
    hmac_secret: str | None = None,
) -> dict[str, Any]:
    url = str(webhook_url or "").strip()
    if not url:
        return {"ok": False, "error": "missing_webhook_url"}
    body = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
    headers: dict[str, str] = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "invariant-agent-alert/0.1",
    }
    secret = str(hmac_secret or "").strip()
    if secret:
        digest = hmac.new(secret.encode("utf-8"), body, "sha256").hexdigest()
        headers["X-Invariant-Signature"] = f"sha256={digest}"
    req = Request(url, data=body, method="POST", headers=headers)
    try:
        with urlopen(req, timeout=max(0.1, float(timeout_s))) as resp:
            code = int(getattr(resp, "status", 200))
            return {"ok": 200 <= code < 300, "status_code": code}
    except HTTPError as e:
        return {"ok": False, "status_code": int(getattr(e, "code", 500)), "error": str(e)}
    except URLError as e:
        return {"ok": False, "error": f"URLError: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{e.__class__.__name__}: {e}"}


def _agent_run_replay_sampler_loop(
    *,
    runtime_host: RuntimeHost,
    stop_event: threading.Event,
    sampler_config: Mapping[str, Any],
    max_cycles: int | None = None,
) -> None:
    cfg = dict(sampler_config)
    if not bool(cfg.get("enabled", False)):
        log.info("Agent run replay sampler disabled by config")
        return

    poll_s = _coerce_float(cfg.get("poll_interval_s"), default=60.0, minimum=1.0)
    scan_limit = _coerce_int(cfg.get("scan_limit"), default=200, minimum=1)
    max_runs = _coerce_int(cfg.get("max_runs"), default=20, minimum=1)
    cooldown_s = _coerce_float(cfg.get("cooldown_seconds"), default=900.0, minimum=0.0)
    strict = _coerce_bool(cfg.get("strict"), default=True)
    policy = str(cfg.get("policy") or "ephemeral").strip().lower()
    if policy not in {"ephemeral", "managed"}:
        policy = "ephemeral"
    ttl_seconds = _coerce_int(cfg.get("ttl_seconds"), default=1200, minimum=1)
    profile = str(cfg.get("profile") or "")
    cycles = 0

    log.info(
        "Agent run replay sampler started profile=%s poll_interval_s=%.2f scan_limit=%s max_runs=%s cooldown_seconds=%.1f strict=%s",
        profile or "default",
        poll_s,
        scan_limit,
        max_runs,
        cooldown_s,
        strict,
    )

    while not stop_event.is_set():
        cycles += 1
        try:
            resp = runtime_host.handle(
                {
                    "cmd": "agent_run_replay_batch",
                    "limit": int(scan_limit),
                    "max_runs": int(max_runs),
                    "cooldown_seconds": float(cooldown_s),
                    "strict": bool(strict),
                    "policy": policy,
                    "ttl_seconds": int(ttl_seconds),
                }
            )
        except Exception:
            log.exception("Agent run replay sampler crashed on runtime_host.handle")
            stop_event.wait(min(5.0, poll_s))
            if max_cycles is not None and cycles >= int(max_cycles):
                break
            continue

        if not isinstance(resp, Mapping):
            log.warning("Agent run replay sampler received non-object response: %r", resp)
            stop_event.wait(min(5.0, poll_s))
            if max_cycles is not None and cycles >= int(max_cycles):
                break
            continue

        if not bool(resp.get("ok", False)):
            err = str(resp.get("error") or "")
            msg = str(resp.get("message") or "")
            if err == "invalid_command":
                log.warning("Agent run replay sampler disabled by runtime host: %s", msg or err)
                break
            log.warning("Agent run replay sampler call failed error=%s message=%s", err or "unknown", msg)
            stop_event.wait(min(5.0, poll_s))
            if max_cycles is not None and cycles >= int(max_cycles):
                break
            continue

        batch = resp.get("batch")
        if not isinstance(batch, Mapping):
            log.warning("Agent run replay sampler missing batch payload: %r", resp)
            stop_event.wait(min(5.0, poll_s))
            if max_cycles is not None and cycles >= int(max_cycles):
                break
            continue

        attempted = int(batch.get("attempted_runs") or 0)
        passed = int(batch.get("passed_runs") or 0)
        failed = int(batch.get("failed_runs") or 0)
        selected = int(batch.get("selected_runs") or 0)
        if attempted > 0 or cycles <= 3 or cycles % 20 == 0:
            log.info(
                "Agent run replay sampler cycle=%s selected=%s attempted=%s passed=%s failed=%s",
                cycles,
                selected,
                attempted,
                passed,
                failed,
            )

        stop_event.wait(poll_s)
        if max_cycles is not None and cycles >= int(max_cycles):
            break

    log.info("Agent run replay sampler stopped cycles=%s profile=%s", cycles, profile or "default")


def _agent_run_alert_loop(
    *,
    runtime_host: RuntimeHost,
    stop_event: threading.Event,
    alert_config: Mapping[str, Any],
    notifier: Callable[..., Mapping[str, Any]] | None = None,
    max_cycles: int | None = None,
) -> None:
    cfg = dict(alert_config)
    webhook_url = str(cfg.get("webhook_url") or "").strip()
    if not webhook_url:
        log.warning("Agent run alert loop disabled: webhook URL is empty")
        return

    notify = notifier or _send_alert_webhook
    poll_s = _coerce_float(cfg.get("poll_interval_s"), default=30.0, minimum=1.0)
    timeout_s = _coerce_float(cfg.get("timeout_s"), default=5.0, minimum=0.1)
    cooldown_s = _coerce_int(cfg.get("cooldown_s"), default=300, minimum=0)
    scan_limit = _coerce_int(cfg.get("scan_limit"), default=1000, minimum=1)
    fail_spike_window = _coerce_int(cfg.get("fail_spike_window"), default=20, minimum=1)
    fail_spike_threshold_pct = _coerce_float(cfg.get("fail_spike_threshold_pct"), default=40.0, minimum=0.0)
    stuck_run_seconds = _coerce_int(cfg.get("stuck_run_seconds"), default=1800, minimum=1)
    replay_target_pct = _coerce_float(cfg.get("replay_target_pct"), default=95.0, minimum=0.0)
    trace_target_pct = _coerce_float(cfg.get("trace_target_pct"), default=100.0, minimum=0.0)
    state_path = Path(str(cfg.get("state_path") or ".invariant/run_registry/run_factory/alerts_state.json")).resolve()
    hmac_secret = str(cfg.get("hmac_secret") or "").strip() or None
    profile = str(cfg.get("profile") or "")
    cycles = 0

    log.info(
        "Agent run alert loop started profile=%s poll_interval_s=%.2f cooldown_s=%s webhook=%s",
        profile or "default",
        poll_s,
        cooldown_s,
        webhook_url,
    )

    while not stop_event.is_set():
        cycles += 1
        try:
            resp = runtime_host.handle(
                {
                    "cmd": "agent_run_observability",
                    "limit": int(scan_limit),
                    "fail_spike_window": int(fail_spike_window),
                    "fail_spike_threshold_pct": float(fail_spike_threshold_pct),
                    "stuck_run_seconds": int(stuck_run_seconds),
                    "replay_target_pct": float(replay_target_pct),
                    "trace_target_pct": float(trace_target_pct),
                }
            )
        except Exception:
            log.exception("Agent run alert loop failed to query observability")
            stop_event.wait(min(5.0, poll_s))
            if max_cycles is not None and cycles >= int(max_cycles):
                break
            continue

        if not isinstance(resp, Mapping):
            log.warning("Agent run alert loop received non-object response: %r", resp)
            stop_event.wait(min(5.0, poll_s))
            if max_cycles is not None and cycles >= int(max_cycles):
                break
            continue

        if not bool(resp.get("ok", False)):
            err = str(resp.get("error") or "")
            msg = str(resp.get("message") or "")
            if err == "invalid_command":
                log.warning("Agent run alert loop disabled by runtime host: %s", msg or err)
                break
            log.warning("Agent run alert loop host call failed error=%s message=%s", err or "unknown", msg)
            stop_event.wait(min(5.0, poll_s))
            if max_cycles is not None and cycles >= int(max_cycles):
                break
            continue

        observability = resp.get("observability")
        if not isinstance(observability, Mapping):
            log.warning("Agent run alert loop missing observability payload: %r", resp)
            stop_event.wait(min(5.0, poll_s))
            if max_cycles is not None and cycles >= int(max_cycles):
                break
            continue

        alerts_raw = observability.get("alerts")
        triggered_alerts = [
            dict(a)
            for a in (alerts_raw or [])
            if isinstance(a, Mapping) and bool(a.get("triggered", False))
        ]
        now = float(time.time())
        state = _read_alert_state(state_path)
        existing_alerts = state.get("alerts")
        state_alerts: dict[str, dict[str, Any]] = (
            {str(k): dict(v) for k, v in existing_alerts.items() if isinstance(v, Mapping)}
            if isinstance(existing_alerts, Mapping)
            else {}
        )
        triggered_ids = {str(a.get("id") or "") for a in triggered_alerts if str(a.get("id") or "").strip()}
        state_changed = False
        for old_id in [k for k in state_alerts.keys() if k not in triggered_ids]:
            state_alerts.pop(old_id, None)
            state_changed = True

        if triggered_alerts:
            sent = 0
            suppressed = 0
            failed = 0
            for alert in triggered_alerts:
                alert_id = str(alert.get("id") or "").strip()
                if not alert_id:
                    continue
                fingerprint = _alert_fingerprint(alert)
                prev = state_alerts.get(alert_id, {})
                prev_fp = str(prev.get("fingerprint") or "")
                prev_at = _coerce_float(prev.get("last_sent_at_unix"), default=0.0, minimum=0.0)
                if prev_fp == fingerprint and (now - prev_at) < float(cooldown_s):
                    suppressed += 1
                    continue

                payload = {
                    "source": "invariant.run_factory.alert",
                    "ts_unix": now,
                    "profile": profile or None,
                    "alert_id": alert_id,
                    "severity": str(alert.get("severity") or "warn"),
                    "alert": dict(alert),
                    "observability": {
                        "ok": bool(observability.get("ok", False)),
                        "generated_at_unix": observability.get("generated_at_unix"),
                        "metrics": dict(observability.get("metrics") or {})
                        if isinstance(observability.get("metrics"), Mapping)
                        else {},
                    },
                }
                send_out = notify(
                    webhook_url=webhook_url,
                    payload=payload,
                    timeout_s=timeout_s,
                    hmac_secret=hmac_secret,
                )
                if bool((send_out or {}).get("ok", False)):
                    sent += 1
                    state_alerts[alert_id] = {
                        "last_sent_at_unix": now,
                        "fingerprint": fingerprint,
                        "last_status_code": send_out.get("status_code"),
                    }
                    state_changed = True
                else:
                    failed += 1
                    log.warning(
                        "Agent run alert delivery failed alert_id=%s payload=%s",
                        alert_id,
                        dict(send_out or {}),
                    )
            if sent > 0 or failed > 0:
                log.info(
                    "Agent run alert loop delivered sent=%s failed=%s suppressed=%s triggered=%s",
                    sent,
                    failed,
                    suppressed,
                    len(triggered_alerts),
                )
        if state_changed:
            _write_alert_state(state_path, {"alerts": state_alerts})

        stop_event.wait(poll_s)
        if max_cycles is not None and cycles >= int(max_cycles):
            break

    log.info("Agent run alert loop stopped cycles=%s profile=%s", cycles, profile or "default")


def run_daemon(
    *,
    factory: Callable[[], Any],
    bind: str,
    port: int,
    admin_token: str | None,
    api_token: str | None,
    max_body_bytes: int,
    enable_watchdog: bool,
    log_request_bodies: bool,
    max_concurrent_requests: int,
    acquire_timeout_ms: int,
    plugin_call_timeout_s: float,
    plugin_call_workers: int,
    plugin_call_queue_size: int,
    per_client_rps: float,
    per_client_burst: int,
    policy_profile: str | None,
    run_worker_enabled: bool,
    run_worker_id: str | None,
    run_worker_lease_seconds: int,
    run_worker_poll_interval_s: float,
    run_worker_reclaim_expired: bool,
) -> int:
    service = factory()
    alert_config = _resolve_agent_run_alerting_config(service=service, policy_profile=policy_profile)
    replay_sampler_config = _resolve_agent_run_replay_sampler_config(service=service, policy_profile=policy_profile)
    rh = RuntimeHost(
        plugin_service=service,
        enable_watchdog=enable_watchdog,
        plugin_call_workers=int(plugin_call_workers),
        plugin_call_queue_size=int(plugin_call_queue_size),
        default_plugin_call_timeout_s=float(plugin_call_timeout_s),
        policy_profile=policy_profile,
    )

    server = InvariantHTTPServer(
        (bind, port),
        JsonAPIHandler,
        runtime_host=rh,
        admin_token=admin_token,
        api_token=api_token,
        max_body_bytes=max_body_bytes,
        log_request_bodies=log_request_bodies,
        max_concurrent_requests=int(max_concurrent_requests),
        acquire_timeout_s=float(int(acquire_timeout_ms)) / 1000.0,
        per_client_rps=float(per_client_rps),
        per_client_burst=int(per_client_burst),
        run_worker_enabled=bool(run_worker_enabled),
        run_worker_id=(str(run_worker_id or "").strip() or None),
        run_worker_lease_seconds=max(1, int(run_worker_lease_seconds)),
        run_worker_poll_interval_s=max(0.05, float(run_worker_poll_interval_s)),
        run_worker_reclaim_expired=bool(run_worker_reclaim_expired),
        run_alerting_enabled=bool(alert_config.get("enabled", False)),
        run_alerting_poll_interval_s=float(alert_config.get("poll_interval_s", 30.0)),
        run_alerting_webhook_url=(str(alert_config.get("webhook_url") or "").strip() or None),
        run_alerting_cooldown_s=int(alert_config.get("cooldown_s", 300)),
        run_replay_sampler_enabled=bool(replay_sampler_config.get("enabled", False)),
        run_replay_sampler_poll_interval_s=float(replay_sampler_config.get("poll_interval_s", 60.0)),
        run_replay_sampler_scan_limit=int(replay_sampler_config.get("scan_limit", 200)),
        run_replay_sampler_batch_size=int(replay_sampler_config.get("max_runs", 20)),
        run_replay_sampler_cooldown_s=float(replay_sampler_config.get("cooldown_seconds", 900.0)),
        run_replay_sampler_strict=bool(replay_sampler_config.get("strict", True)),
    )

    stop_event = threading.Event()
    worker_thread: threading.Thread | None = None
    alert_thread: threading.Thread | None = None
    replay_sampler_thread: threading.Thread | None = None

    def _shutdown(_signum: int, _frame: Any) -> None:
        if stop_event.is_set():
            return
        stop_event.set()
        try:
            server.shutdown()
        except Exception:
            pass

    # best-effort signals
    try:
        signal.signal(signal.SIGINT, _shutdown)
    except Exception:
        pass
    try:
        signal.signal(signal.SIGTERM, _shutdown)
    except Exception:
        pass

    log.info("Invariant daemon listening on http://%s:%d", bind, port)
    if admin_token:
        log.info("Admin token enabled for /v1/shutdown")
    elif api_token:
        log.info("Admin token not set; /v1/shutdown requires API token")
    else:
        log.info("Admin token not set; /v1/shutdown enabled without auth token")

    if bool(run_worker_enabled):
        worker_thread = threading.Thread(
            target=_agent_run_queue_worker_loop,
            name="invariant-agent-run-worker",
            daemon=True,
            kwargs={
                "runtime_host": rh,
                "stop_event": stop_event,
                "worker_id": (str(run_worker_id or "").strip()),
                "lease_seconds": int(run_worker_lease_seconds),
                "poll_interval_s": float(run_worker_poll_interval_s),
                "reclaim_expired": bool(run_worker_reclaim_expired),
            },
        )
        worker_thread.start()

    if bool(replay_sampler_config.get("enabled", False)):
        replay_sampler_thread = threading.Thread(
            target=_agent_run_replay_sampler_loop,
            name="invariant-agent-run-replay-sampler",
            daemon=True,
            kwargs={
                "runtime_host": rh,
                "stop_event": stop_event,
                "sampler_config": dict(replay_sampler_config),
            },
        )
        replay_sampler_thread.start()
    elif bool(replay_sampler_config.get("configured_enabled", False)):
        log.warning("Agent run replay sampler requested by policy but disabled by resolved config")

    if bool(alert_config.get("enabled", False)):
        alert_thread = threading.Thread(
            target=_agent_run_alert_loop,
            name="invariant-agent-run-alerts",
            daemon=True,
            kwargs={
                "runtime_host": rh,
                "stop_event": stop_event,
                "alert_config": dict(alert_config),
            },
        )
        alert_thread.start()
    elif bool(alert_config.get("configured_enabled", False)):
        log.warning(
            "Agent run alerting requested by policy but disabled due to missing webhook_url profile=%s",
            str(alert_config.get("profile") or ""),
        )

    try:
        server.serve_forever(poll_interval=0.5)
        return 0
    finally:
        stop_event.set()
        if worker_thread is not None:
            try:
                worker_thread.join(timeout=3.0)
            except Exception:
                pass
        if alert_thread is not None:
            try:
                alert_thread.join(timeout=3.0)
            except Exception:
                pass
        if replay_sampler_thread is not None:
            try:
                replay_sampler_thread.join(timeout=3.0)
            except Exception:
                pass
        # Make shutdown deterministic
        try:
            rh.close()
        except Exception:
            log.exception("Failed to close RuntimeHost")
        try:
            server.server_close()
        except Exception:
            log.exception("Failed to close server socket")
        log.info("Daemon stopped.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="invariant-daemon")
    run_worker_enabled_default = _env_bool("INVARIANT_RUN_WORKER_ENABLED", False)
    run_worker_reclaim_default = _env_bool("INVARIANT_RUN_WORKER_RECLAIM_EXPIRED", True)

    p.add_argument("--factory", default=os.environ.get("INVARIANT_FACTORY", ""), help="module:function")
    p.add_argument("--bind", default=os.environ.get("INVARIANT_BIND", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("INVARIANT_PORT", "8088")))

    p.add_argument("--admin-token", default=os.environ.get("INVARIANT_ADMIN_TOKEN", ""))
    p.add_argument("--api-token", default=os.environ.get("INVARIANT_API_TOKEN", ""))
    p.add_argument("--allow-unauthenticated", action="store_true", help="allow running without api_token (unsafe)")
    p.add_argument("--max-body-bytes", type=int, default=int(os.environ.get("INVARIANT_MAX_BODY", "1048576")))
    p.add_argument("--log-level", default=os.environ.get("INVARIANT_LOG_LEVEL", "INFO"))
    p.add_argument("--no-watchdog", action="store_true", help="disable background watchdog thread")
    p.add_argument("--log-request-bodies", action="store_true", help="log request body previews on exceptions (dangerous)")
    p.add_argument("--max-concurrent-requests", type=int, default=int(os.environ.get("INVARIANT_MAX_CONCURRENT", "64")))
    p.add_argument("--acquire-timeout-ms", type=int, default=int(os.environ.get("INVARIANT_ACQUIRE_TIMEOUT_MS", "250")))
    p.add_argument("--per-client-rps", type=float, default=float(os.environ.get("INVARIANT_PER_CLIENT_RPS", "0")))
    p.add_argument("--per-client-burst", type=int, default=int(os.environ.get("INVARIANT_PER_CLIENT_BURST", "0")))
    p.add_argument("--plugin-call-timeout-s", type=float, default=float(os.environ.get("INVARIANT_PLUGIN_CALL_TIMEOUT_S", "10")))
    p.add_argument("--plugin-call-workers", type=int, default=int(os.environ.get("INVARIANT_PLUGIN_CALL_WORKERS", "16")))
    p.add_argument("--plugin-call-queue-size", type=int, default=int(os.environ.get("INVARIANT_PLUGIN_CALL_QUEUE_SIZE", "128")))
    p.add_argument("--run-worker-enabled", dest="run_worker_enabled", action="store_true", help="enable daemon-side agent run queue worker")
    p.add_argument("--no-run-worker", dest="run_worker_enabled", action="store_false", help="disable daemon-side agent run queue worker")
    p.add_argument("--run-worker-id", default=os.environ.get("INVARIANT_RUN_WORKER_ID", ""))
    p.add_argument("--run-worker-lease-seconds", type=int, default=int(os.environ.get("INVARIANT_RUN_WORKER_LEASE_SECONDS", "120")))
    p.add_argument("--run-worker-poll-interval-s", type=float, default=float(os.environ.get("INVARIANT_RUN_WORKER_POLL_INTERVAL_S", "0.5")))
    p.add_argument("--run-worker-reclaim-expired", dest="run_worker_reclaim_expired", action="store_true")
    p.add_argument("--no-run-worker-reclaim-expired", dest="run_worker_reclaim_expired", action="store_false")
    p.set_defaults(run_worker_enabled=run_worker_enabled_default, run_worker_reclaim_expired=run_worker_reclaim_default)
    p.add_argument(
        "--policy-profile",
        default=os.environ.get("INVARIANT_POLICY_PROFILE", ""),
        help="sandbox policy profile for executions: dev|stage|prod (empty = default read-only)",
    )

    args = p.parse_args(argv)

    _setup_logging(args.log_level)

    if args.factory:
        factory = _parse_factory(args.factory)
    else:
        factory = _default_factory

    admin_token = args.admin_token.strip() or None
    api_token = args.api_token.strip() or None
    # convenience: if api_token not set but admin_token is set, reuse it
    if api_token is None and admin_token is not None:
        api_token = admin_token

    # Secure-by-default: forbid unauthenticated daemon on non-loopback bind.
    bind = (args.bind or "").strip()
    is_loopback = bind in ("127.0.0.1", "localhost", "::1")
    if not is_loopback and api_token is None and not args.allow_unauthenticated:
        log.error("Refusing to start without api_token on non-loopback bind=%r. Set --api-token or --allow-unauthenticated.", bind)
        return 2

    return run_daemon(
        factory=factory,
        bind=args.bind,
        port=args.port,
        admin_token=admin_token,
        api_token=api_token,
        max_body_bytes=args.max_body_bytes,
        enable_watchdog=not args.no_watchdog,
        log_request_bodies=bool(args.log_request_bodies),
        max_concurrent_requests=int(args.max_concurrent_requests),
        acquire_timeout_ms=int(args.acquire_timeout_ms),
        plugin_call_timeout_s=float(args.plugin_call_timeout_s),
        plugin_call_workers=int(args.plugin_call_workers),
        plugin_call_queue_size=int(args.plugin_call_queue_size),
        per_client_rps=float(args.per_client_rps),
        per_client_burst=int(args.per_client_burst),
        policy_profile=(str(args.policy_profile).strip() or None),
        run_worker_enabled=bool(args.run_worker_enabled),
        run_worker_id=(str(args.run_worker_id).strip() or None),
        run_worker_lease_seconds=max(1, int(args.run_worker_lease_seconds)),
        run_worker_poll_interval_s=max(0.05, float(args.run_worker_poll_interval_s)),
        run_worker_reclaim_expired=bool(args.run_worker_reclaim_expired),
    )


if __name__ == "__main__":
    raise SystemExit(main())
