from __future__ import annotations

import http.client
import json
from dataclasses import dataclass
import socket
from typing import Any, Dict, Optional, Tuple


@dataclass
class HttpResp:
    status: int
    headers: Dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


def http_json(
    host: str,
    port: int,
    method: str,
    path: str,
    body_obj: Optional[dict] = None,
    *,
    headers: Optional[Dict[str, str]] = None,
    expect_continue: bool = False,
    timeout: float = 3.0,
) -> HttpResp:
    conn = http.client.HTTPConnection(host, port, timeout=timeout)

    raw = b""
    h: Dict[str, str] = {}
    if headers:
        h.update(headers)

    if body_obj is not None:
        raw = json.dumps(body_obj).encode("utf-8")
        h.setdefault("Content-Type", "application/json")
        h["Content-Length"] = str(len(raw))
        if expect_continue:
            h.setdefault("Expect", "100-continue")
    else:
        h.setdefault("Content-Length", "0")

    conn.request(method, path, body=raw, headers=h)
    resp = conn.getresponse()
    data = resp.read()
    out_headers = {k.lower(): v for (k, v) in resp.getheaders()}
    conn.close()
    return HttpResp(status=resp.status, headers=out_headers, body=data)

def _read_http_response(sock: socket.socket, *, timeout: float = 3.0) -> HttpResp:
    sock.settimeout(timeout)
    buf = bytearray()

    # read headers
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf.extend(chunk)

    head, _, rest = bytes(buf).partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    if not lines:
        return HttpResp(status=0, headers={}, body=rest)

    # status line: HTTP/1.1 200 OK
    try:
        parts = lines[0].decode("iso-8859-1").split(" ", 2)
        status = int(parts[1])
    except Exception:
        status = 0

    headers: Dict[str, str] = {}
    for ln in lines[1:]:
        if b":" in ln:
            k, v = ln.split(b":", 1)
            headers[k.decode("iso-8859-1").strip().lower()] = v.decode("iso-8859-1").strip()

    body = bytearray(rest)
    # read body by Content-Length if present; else read until close
    clen = headers.get("content-length")
    if clen is not None:
        try:
            need = int(clen) - len(body)
        except Exception:
            need = 0
        while need > 0:
            chunk = sock.recv(min(4096, need))
            if not chunk:
                break
            body.extend(chunk)
            need -= len(chunk)
    else:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            body.extend(chunk)

    return HttpResp(status=status, headers=headers, body=bytes(body))


def http_json_expect_continue_raw(
    host: str,
    port: int,
    path: str,
    body_obj: dict,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 3.0,
) -> HttpResp:
    """
    Correct Expect: 100-continue flow (stable on Windows):
      1) send only headers with Expect: 100-continue
      2) read response
      3) send body only if server replies 100
      4) read final response
    """
    raw = json.dumps(body_obj).encode("utf-8")
    h = {
        "host": f"{host}:{port}",
        "connection": "close",
        "content-type": "application/json",
        "content-length": str(len(raw)),
        "expect": "100-continue",
    }
    if headers:
        for k, v in headers.items():
            h[str(k).lower()] = str(v)

    req_lines = [f"POST {path} HTTP/1.1"]
    for k, v in h.items():
        req_lines.append(f"{k.title()}: {v}")
    req = ("\r\n".join(req_lines) + "\r\n\r\n").encode("iso-8859-1")

    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(req)
        r1 = _read_http_response(s, timeout=timeout)
        if r1.status == 100:
            s.sendall(raw)
            return _read_http_response(s, timeout=timeout)
        return r1