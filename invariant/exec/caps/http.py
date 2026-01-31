# invariant/exec/caps/http.py
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit
import json
import ssl
import urllib.request
import urllib.error
import socket
import ipaddress

from invariant.exec.caps.audit import CapabilityAudit


class HttpAccessError(RuntimeError): ...
class HttpHostDenied(HttpAccessError): ...
class HttpReadLimitExceeded(HttpAccessError): ...


class HttpStatusError(HttpAccessError):
    def __init__(self, status: int, reason: str | None = None):
        super().__init__(f"HTTP status {status}" + (f": {reason}" if reason else ""))
        self.status = status
        self.reason = reason


def _safe_url(url: str) -> str:
    # Do NOT log query/fragment (tokens, secrets).
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))

def _is_public_ip(host: str) -> bool:
    # Resolve host and block private/loopback/link-local/etc.
    try:
        # If host is already an IP literal:
        ip = ipaddress.ip_address(host)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        # If resolution fails, treat as unsafe (containment).
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return False
    return True

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HttpAccessError("HTTP redirects are disabled")
    
class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    """
    Redirect handler that re-checks every redirect target via HttpAccess._check_url.
    This prevents bypassing host allowlist / private IP blocks via 30x.
    """
    def __init__(self, check_url):
        super().__init__()
        self._check_url = check_url

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self._check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def _filtered_headers(h: Mapping[str, str] | None) -> Mapping[str, str]:
    if not h:
        return {}
    out: dict[str, str] = {}
    for k, v in h.items():
        kk = str(k).strip()
        if not kk:
            continue
        lk = kk.lower()
        # prevent host/request-smuggling weirdness
        if lk in ("host", "content-length", "connection", "transfer-encoding"):
            continue
        out[kk] = str(v)
    return out

class _NoopSpan:
    def __enter__(self) -> "_NoopSpan":
        return self
    def add(self, **_fields: Any) -> None:
        return
    def __exit__(self, *_exc) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class HttpAccess:
    allow_hosts: tuple[str, ...]
    timeout_s: float = 10.0
    max_bytes: int = 2_000_000
    allow_http: bool = False
    allow_any_host: bool = False
    allow_redirects: bool = False
    allow_private_ips: bool = False
    user_agent: str = "InvariantHttp/0.1"
    cap_id: str | None = None
    _audit: CapabilityAudit | None = None

    def bind_audit(self, audit: CapabilityAudit, *, cap_id: str) -> "HttpAccess":
        return replace(self, _audit=audit, cap_id=cap_id)

    def _span(self, op: str, payload: dict) -> Any:
        a = self._audit
        if a is None:
            return _NoopSpan()
        return a.span(f"cap.http.{op}", cap_id=(self.cap_id or "invariant:capability.<unknown>"), payload=payload)

    def _check_url(self, url: str) -> None:
        parts = urlsplit(url)
        scheme = (parts.scheme or "").lower()
        host = (parts.hostname or "").lower()

        if scheme not in ("https", "http"):
            raise HttpAccessError(f"Unsupported scheme: {scheme!r}")
        if scheme == "http" and not self.allow_http:
            raise HttpAccessError("Plain HTTP is disabled (https only)")
        if parts.username or parts.password:
            raise HttpAccessError("Userinfo in URL is not allowed")
        if not host:
            raise HttpAccessError("URL has no hostname")

        allowed = {h.lower() for h in (self.allow_hosts or ())}
        if not allowed and not self.allow_any_host:
            raise HttpHostDenied("Host allowlist is empty (deny-all)")
        if allowed and host not in allowed:
            raise HttpHostDenied(f"Host not allowed: {host}")

        if not self.allow_private_ips:
            if not _is_public_ip(host):
                raise HttpHostDenied("Host resolves to private/unsafe IP")
        
    def _open(self, req: urllib.request.Request, *, ctx: ssl.SSLContext):
        handlers = [urllib.request.ProxyHandler({})]  # disable env proxies

        if self.allow_redirects:
            handlers.append(_CheckedRedirect(self._check_url))
        else:
            handlers.append(_NoRedirect())

        # ensure our SSL context is used
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
        handlers.append(urllib.request.HTTPHandler())

        opener = urllib.request.build_opener(*handlers)
        return opener.open(req, timeout=float(self.timeout_s))

    def _read_limited(self, resp) -> bytes:
        status = getattr(resp, "status", None)
        if isinstance(status, int) and status >= 400:
            raise HttpStatusError(status, getattr(resp, "reason", None))

        cl = resp.headers.get("Content-Length")
        if cl is not None:
            try:
                if int(cl) > int(self.max_bytes):
                    raise HttpReadLimitExceeded(f"Content-Length {cl} > max_bytes {self.max_bytes}")
            except ValueError:
                pass

        limit = int(self.max_bytes)
        out = bytearray()
        while True:
            chunk = resp.read(min(64 * 1024, limit - len(out) + 1))
            if not chunk:
                break
            out.extend(chunk)
            if len(out) > limit:
                raise HttpReadLimitExceeded(f"Response exceeded max_bytes {self.max_bytes}")
        return bytes(out)

    def _get_bytes_impl(self, url: str, *, headers: Mapping[str, str] | None) -> Tuple[bytes, Optional[int]]:
        self._check_url(url)
        req = urllib.request.Request(url=url, method="GET")
        req.add_header("User-Agent", self.user_agent)
        for k, v in _filtered_headers(headers).items():
            req.add_header(str(k), str(v))
        ctx = ssl.create_default_context()
        try:
            with self._open(req, ctx=ctx) as resp:
                data = self._read_limited(resp)
                return data, getattr(resp, "status", None)
        except urllib.error.HTTPError as e:
            status = getattr(e, "code", None)
            if isinstance(status, int) and status >= 400:
                raise HttpStatusError(status, getattr(e, "reason", None)) from e
            raise HttpAccessError(str(e)) from e
        except HttpAccessError:
            raise
        except Exception as e:
            raise HttpAccessError(str(e)) from e

    def get_bytes(self, url: str, *, headers: Mapping[str, str] | None = None) -> bytes:
        parts = urlsplit(url)
        with self._span(
            "get_bytes",
            {
                "url": _safe_url(url),
                "host": (parts.hostname or ""),
                "path": parts.path,
                "max_bytes": int(self.max_bytes),
                "timeout_s": float(self.timeout_s),
            },
        ) as sp:
            data, status = self._get_bytes_impl(url, headers=headers)
            sp.add(bytes=len(data), status=status)
            return data

    def get_text(self, url: str, *, encoding: str = "utf-8", errors: str = "strict") -> str:
        parts = urlsplit(url)
        with self._span(
            "get_text",
            {"url": _safe_url(url), "host": (parts.hostname or ""), "path": parts.path, "encoding": encoding},
        ) as sp:
            data, status = self._get_bytes_impl(url, headers=None)
            s = data.decode(encoding, errors=errors)
            sp.add(bytes=len(data), chars=len(s), status=status)
            return s

    def get_json(self, url: str) -> Any:
        parts = urlsplit(url)
        with self._span("get_json", {"url": _safe_url(url), "host": (parts.hostname or ""), "path": parts.path}) as sp:
            data, status = self._get_bytes_impl(url, headers=None)
            # strict utf-8 for json
            text = data.decode("utf-8", errors="strict")
            obj = json.loads(text)
            sp.add(bytes=len(data), status=status, json_type=type(obj).__name__)
            return obj

    def post_json(self, url: str, payload: Any, *, headers: Mapping[str, str] | None = None) -> Any:
        parts = urlsplit(url)
        body = json.dumps(payload).encode("utf-8")
        with self._span(
            "post_json",
            {
                "url": _safe_url(url),
                "host": (parts.hostname or ""),
                "path": parts.path,
                "body_bytes": len(body),
                "max_bytes": int(self.max_bytes),
                "timeout_s": float(self.timeout_s),
            },
        ) as sp:
            self._check_url(url)
            if len(body) > int(self.max_bytes):
                raise HttpAccessError("Request body too large")
            req = urllib.request.Request(url=url, data=body, method="POST")
            req.add_header("User-Agent", self.user_agent)
            req.add_header("Content-Type", "application/json; charset=utf-8")
            for k, v in _filtered_headers(headers).items():
                req.add_header(str(k), str(v))

            ctx = ssl.create_default_context()
            try:
                with self._open(req, ctx=ctx) as resp:
                    data = self._read_limited(resp)
                    status = getattr(resp, "status", None)
                    obj = json.loads(data.decode("utf-8", errors="strict"))
                    sp.add(bytes=len(data), status=status, json_type=type(obj).__name__)
                    return obj
            except urllib.error.HTTPError as e:
                status = getattr(e, "code", None)
                if isinstance(status, int) and status >= 400:
                    raise HttpStatusError(status, getattr(e, "reason", None)) from e
                raise HttpAccessError(str(e)) from e
            except HttpAccessError:
                raise
            except Exception as e:
                raise HttpAccessError(str(e)) from e
