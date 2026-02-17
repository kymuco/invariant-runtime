# tests/test_capability_audit_events.py
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from invariant.exec.caps.audit import CapabilityAudit
from invariant.exec.caps.fs import FSAccess, FSPathTraversalBlocked

# если файл capability_http.py у тебя в invariant/exec/
from invariant.exec.caps.http import HttpAccess, HttpAccessError, HttpHostDenied
from invariant.exec.caps.kv import KVCorruptError, KVStore


class Collector:
    """
    Captures emitted events like runtime.emit_event(name, **kwargs).
    """
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, name: str, **kwargs: Any) -> None:
        self.events.append((name, dict(kwargs)))

    def find(self, prefix: str) -> list[tuple[str, dict[str, Any]]]:
        return [(n, k) for (n, k) in self.events if n.startswith(prefix)]


def test_audit_span_success_emits_ok_true() -> None:
    c = Collector()
    a = CapabilityAudit(emit=c.emit)

    with a.span("cap.test.ok", cap_id="invariant:capability.test", payload={"x": 1}):
        pass

    assert len(c.events) == 1
    name, ev = c.events[0]
    assert name == "cap.test.ok"
    assert ev["cap_id"] == "invariant:capability.test"
    assert ev["ok"] is True
    assert isinstance(ev["dt_ms"], float)
    assert ev["dt_ms"] >= 0.0
    assert ev["payload"]["x"] == 1
    assert ev["error_type"] is None
    assert ev["error"] is None


def test_audit_span_exception_emits_ok_false_and_error() -> None:
    c = Collector()
    a = CapabilityAudit(emit=c.emit)

    with pytest.raises(ValueError):
        with a.span("cap.test.fail", cap_id="invariant:capability.test", payload={"op": "boom"}):
            raise ValueError("boom")

    assert len(c.events) == 1
    name, ev = c.events[0]
    assert name == "cap.test.fail"
    assert ev["ok"] is False
    assert ev["error_type"] == "ValueError"
    assert "boom" in (ev["error"] or "")


def test_payload_is_sanitized_and_bounded() -> None:
    c = Collector()
    a = CapabilityAudit(emit=c.emit)

    long = "x" * 5000
    with a.span(
        "cap.test.payload",
        cap_id="invariant:capability.test",
        payload={"raw_bytes": b"abc", "long": long, "nested": {"k": b"xyz"}},
    ):
        pass

    name, ev = c.events[0]
    p = ev["payload"]
    assert p["raw_bytes"] == {"bytes_len": 3}
    assert isinstance(p["long"], str)
    assert len(p["long"]) <= 1024 + 1  # с "…" возможно +1
    assert p["nested"]["k"] == {"bytes_len": 3}


def test_fs_audit_emits_events_and_records_sizes() -> None:
    c = Collector()
    audit = CapabilityAudit(emit=c.emit)

    with tempfile.TemporaryDirectory() as td:
        fs = FSAccess(root=Path(td), write=True, delete=True).bind_audit(
            audit, cap_id="invariant:capability.fs_sandbox"
        )

        fs.write_text("a.txt", "hello", atomic=True)
        out = fs.read_text("a.txt")

        assert out == "hello"

    fs_events = c.find("cap.fs.")
    assert fs_events, "Expected FS audit events"

    # Достаточно проверить, что среди событий есть write_bytes и read_bytes
    names = [n for (n, _) in fs_events]
    assert any(n == "cap.fs.write_bytes" for n in names)
    assert any(n == "cap.fs.read_bytes" for n in names)

    # Проверяем наличие sizes в payload хотя бы у одного
    write_ev = next(ev for (n, ev) in fs_events if n == "cap.fs.write_bytes")
    assert write_ev["payload"]["bytes"] == 5


def test_fs_path_traversal_is_audited_as_failure() -> None:
    c = Collector()
    audit = CapabilityAudit(emit=c.emit)

    with tempfile.TemporaryDirectory() as td:
        fs = FSAccess(root=Path(td), write=True).bind_audit(
            audit, cap_id="invariant:capability.fs_sandbox"
        )

        with pytest.raises(FSPathTraversalBlocked):
            fs.read_bytes("../secrets.txt")

    fs_events = c.find("cap.fs.")
    assert fs_events, "Expected FS audit event on failure"
    # Последнее событие — fail
    name, ev = fs_events[-1]
    assert name in ("cap.fs.read_bytes", "cap.fs.read_text")
    assert ev["ok"] is False
    assert ev["error_type"] is not None


def test_kv_audit_emits_and_does_not_log_value() -> None:
    c = Collector()
    audit = CapabilityAudit(emit=c.emit)

    with tempfile.TemporaryDirectory() as td:
        fs = FSAccess(root=Path(td), write=True, delete=True).bind_audit(
            audit, cap_id="invariant:capability.workspace"
        )
        kv = KVStore(fs=fs, rel_path="kv.json").bind_audit(
            audit, cap_id="invariant:capability.kv"
        )

        kv.set("token", "super-secret")
        got = kv.get("token")

        assert got == "super-secret"

    kv_events = c.find("cap.kv.")
    assert kv_events, "Expected KV audit events"
    names = [n for (n, _) in kv_events]
    assert "cap.kv.set" in names
    assert "cap.kv.get" in names

    set_ev = next(ev for (n, ev) in kv_events if n == "cap.kv.set")
    payload = set_ev["payload"]

    # value не должен логироваться, только мета
    assert payload["key"] == "token"
    assert "value_type" in payload
    assert "super-secret" not in json.dumps(payload, ensure_ascii=False)


def test_http_audit_strips_query_and_fragment(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Collector()
    audit = CapabilityAudit(emit=c.emit)

    # ---- fake urllib response ----
    class _Headers(dict):
        def get(self, k: str, default: Any = None) -> Any:
            return super().get(k, default)

    class _Resp:
        def __init__(self, body: bytes):
            self._body = body
            self._read = False
            self.status = 200
            self.reason = "OK"
            self.headers = _Headers({"Content-Length": str(len(body))})

        def read(self, n: int = -1) -> bytes:
            if self._read:
                return b""
            self._read = True
            return self._body

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def fake_build_opener(*_handlers):
            class _Opener:
                def open(self, req, timeout=None):
                    return _Resp(b'{"ok":true}')
            return _Opener()

        monkeypatch.setattr("invariant.exec.caps.http.urllib.request.build_opener", fake_build_opener)

    http = HttpAccess(
        allow_hosts=("example.com",),
        timeout_s=1.0,
        max_bytes=1024,
        allow_http=False,
        allow_private_ips=True,   # чтобы тест не зависел от DNS/socket.getaddrinfo
    ).bind_audit(audit, cap_id="invariant:capability.http")

    url = "https://example.com/api?token=SECRET#frag"
    data = http.get_bytes(url)

    assert data == b'{"ok":true}'

    http_events = c.find("cap.http.")
    assert http_events, "Expected HTTP audit events"

    name, ev = http_events[-1]
    assert name == "cap.http.get_bytes"
    logged_url = ev["payload"]["url"]
    assert "token=SECRET" not in logged_url
    assert "#frag" not in logged_url
    assert logged_url.endswith("/api")


def test_http_host_denied_is_audited(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Collector()
    audit = CapabilityAudit(emit=c.emit)

    # Важно: даже если urlopen никогда не вызовется (deny до сети),
    # событие всё равно должно быть.
    def fake_urlopen(*args, **kwargs):
        raise AssertionError("urlopen must not be called for denied host")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    http = HttpAccess(
        allow_hosts=("allowed.com",),
        timeout_s=1.0,
        max_bytes=1024,
        allow_http=False,
    ).bind_audit(audit, cap_id="invariant:capability.http")

    with pytest.raises(HttpHostDenied):
        http.get_bytes("https://evil.com/path?token=SECRET#frag")

    http_events = c.find("cap.http.")
    assert http_events, "Expected HTTP audit event on deny"

    # Последнее событие — отказ
    name, ev = http_events[-1]
    assert name == "cap.http.get_bytes"
    assert ev["ok"] is False
    assert ev["error_type"] is not None

    # URL в payload должен быть безопасным
    logged_url = ev["payload"]["url"]
    assert "token=SECRET" not in logged_url
    assert "#frag" not in logged_url
    assert logged_url.endswith("/path")


def test_http_plain_http_disabled_is_audited(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Collector()
    audit = CapabilityAudit(emit=c.emit)

    def fake_urlopen(*args, **kwargs):
        raise AssertionError("urlopen must not be called when http is disabled")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    http = HttpAccess(
        allow_hosts=("example.com",),
        timeout_s=1.0,
        max_bytes=1024,
        allow_http=False,  # http запрещён
    ).bind_audit(audit, cap_id="invariant:capability.http")

    with pytest.raises(HttpAccessError):
        http.get_bytes("http://example.com/a?x=1#b")

    http_events = c.find("cap.http.")
    assert http_events, "Expected HTTP audit event on deny"

    name, ev = http_events[-1]
    assert name == "cap.http.get_bytes"
    assert ev["ok"] is False
    assert ev["error_type"] is not None

    logged_url = ev["payload"]["url"]
    assert "x=1" not in logged_url
    assert "#b" not in logged_url
    assert logged_url.endswith("/a")


def test_http_read_limit_exceeded_is_audited(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Collector()
    audit = CapabilityAudit(emit=c.emit)

    # ---- fake urllib response with huge Content-Length ----
    class _Headers(dict):
        def get(self, k: str, default: Any = None) -> Any:
            return super().get(k, default)

    class _Resp:
        def __init__(self):
            self.status = 200
            self.reason = "OK"
            self.headers = _Headers({"Content-Length": "9999999"})
            self._read_called = False

        def read(self, n: int = -1) -> bytes:
            # shouldn't be read because CL denies early
            self._read_called = True
            return b"{}"

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    def fake_urlopen(req, timeout=None, context=None):
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    http = HttpAccess(
        allow_hosts=("example.com",),
        timeout_s=1.0,
        max_bytes=10,  # нарочно маленький лимит
        allow_http=False,
    ).bind_audit(audit, cap_id="invariant:capability.http")

    with pytest.raises(HttpAccessError):
        http.get_bytes("https://example.com/large?token=SECRET#frag")

    http_events = c.find("cap.http.")
    assert http_events, "Expected HTTP audit event on read limit"
    name, ev = http_events[-1]
    assert name == "cap.http.get_bytes"
    assert ev["ok"] is False
    assert ev["error_type"] is not None

    # URL в payload безопасный
    logged_url = ev["payload"]["url"]
    assert "token=SECRET" not in logged_url
    assert "#frag" not in logged_url
    assert logged_url.endswith("/large")

    # Если применил патч payload (max_bytes/timeout_s), то проверяем их:
    assert ev["payload"]["max_bytes"] == 10
    assert ev["payload"]["timeout_s"] == 1.0


def test_audit_sink_exceptions_are_contained() -> None:
    # emit падает — но capability операции должны работать как ни в чём не бывало
    def bad_emit(name: str, **kwargs: Any) -> None:
        raise RuntimeError("sink is down")

    audit = CapabilityAudit(emit=bad_emit)

    with tempfile.TemporaryDirectory() as td:
        fs = FSAccess(root=Path(td), write=True, delete=True).bind_audit(
            audit, cap_id="invariant:capability.fs_sandbox"
        )

        # успешный путь не должен падать из-за аудит-синка
        fs.write_text("ok.txt", "hello")
        assert fs.read_text("ok.txt") == "hello"

        # и ошибочный путь должен продолжать выбрасывать ОРИГИНАЛЬНУЮ ошибку,
        # а не ошибку аудита
        with pytest.raises(FSPathTraversalBlocked):
            fs.read_bytes("../nope.txt")


def test_http_stream_exceeds_max_bytes_is_audited(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Collector()
    audit = CapabilityAudit(emit=c.emit)

    # ---- fake urllib response WITHOUT Content-Length ----
    class _Headers(dict):
        def get(self, k: str, default: Any = None) -> Any:
            return super().get(k, default)

    class _Resp:
        def __init__(self, body: bytes):
            self.status = 200
            self.reason = "OK"
            self.headers = _Headers({})  # no Content-Length
            self._body = body
            self._sent = False

        def read(self, n: int = -1) -> bytes:
            # Simulate one-shot read returning > max_bytes
            if self._sent:
                return b""
            self._sent = True
            return self._body

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    def fake_urlopen(req, timeout=None, context=None):
        # max_bytes=10 => return 11 bytes to force overflow
        return _Resp(b"a" * 11)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    http = HttpAccess(
        allow_hosts=("example.com",),
        timeout_s=1.0,
        max_bytes=10,
        allow_http=False,
    ).bind_audit(audit, cap_id="invariant:capability.http")

    with pytest.raises(HttpAccessError):
        http.get_bytes("https://example.com/stream?token=SECRET#frag")

    http_events = c.find("cap.http.")
    assert http_events, "Expected HTTP audit event on streamed overflow"
    name, ev = http_events[-1]
    assert name == "cap.http.get_bytes"
    assert ev["ok"] is False
    assert ev["error_type"] is not None

    logged_url = ev["payload"]["url"]
    assert "token=SECRET" not in logged_url
    assert "#frag" not in logged_url
    assert logged_url.endswith("/stream")


def test_http_post_body_too_large_is_audited_and_urlopen_not_called(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Collector()
    audit = CapabilityAudit(emit=c.emit)

    def fake_urlopen(*args, **kwargs):
        raise AssertionError("urlopen must not be called when request body is too large")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    http = HttpAccess(
        allow_hosts=("example.com",),
        timeout_s=1.0,
        max_bytes=20,   # маленький лимит
        allow_http=False,
    ).bind_audit(audit, cap_id="invariant:capability.http")

    big_payload = {"data": "x" * 10_000}  # точно > 20 bytes после json

    with pytest.raises(HttpAccessError):
        http.post_json("https://example.com/post?token=SECRET#frag", big_payload)

    http_events = c.find("cap.http.")
    assert http_events, "Expected HTTP audit event on body-too-large"
    name, ev = http_events[-1]
    assert name == "cap.http.post_json"
    assert ev["ok"] is False
    assert ev["error_type"] is not None

    logged_url = ev["payload"]["url"]
    assert "token=SECRET" not in logged_url
    assert "#frag" not in logged_url
    assert logged_url.endswith("/post")

    # Если применил мета-патч (max_bytes/timeout_s/body_bytes) — проверим:
    assert ev["payload"]["max_bytes"] == 20
    assert isinstance(ev["payload"].get("body_bytes"), int)


def test_kv_corrupt_json_is_audited_and_set_recovers() -> None:
    c = Collector()
    audit = CapabilityAudit(emit=c.emit)

    with tempfile.TemporaryDirectory() as td:
        fs = FSAccess(root=Path(td), write=True, delete=True).bind_audit(
            audit, cap_id="invariant:capability.workspace"
        )

        # Пишем заведомо битый JSON
        fs.write_text("kv.json", "{not-json", atomic=True)

        kv = KVStore(fs=fs, rel_path="kv.json", repair_on_write=True).bind_audit(
            audit, cap_id="invariant:capability.kv"
        )

        # get() должен упасть (и это должно аудититься)
        with pytest.raises(KVCorruptError):
            kv.get("x")

        kv_events = c.find("cap.kv.")
        assert kv_events, "Expected KV audit events"

        # Должен быть хотя бы один fail-ивент (load или get)
        fail = [(n, ev) for (n, ev) in kv_events if ev.get("ok") is False]
        assert fail, "Expected KV audit failure on corrupt JSON"

        # Восстановление на set(): должно успешно переписать файл
        kv.set("x", "1")
        assert kv.get("x") == "1"

        # Проверим, что set был ok=True и отметил recovered=True
        kv_events = c.find("cap.kv.")  # refresh after set/get
        set_events = [(n, ev) for (n, ev) in kv_events if n == "cap.kv.set"]
        assert set_events, "Expected cap.kv.set event"
        # берём последний set
        _, set_ev = set_events[-1]
        assert set_ev["ok"] is True
        assert set_ev["payload"].get("recovered") is True
        