from __future__ import annotations

from typing import Any

from invariant.exec.runtime.execution_runtime import ExecutionRuntime
from invariant.spec.modes import InstanceMode


class _DummyStore:
    pass


class _DummyPolicy:
    instance_mode = InstanceMode.EPHEMERAL

    def create_store(self, *, registry):
        return _DummyStore()


class _TracerOnRuntimeEvent:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def on_runtime_event(self, name: str, *, runtime_id: str, **fields: Any) -> None:
        self.events.append((name, runtime_id, dict(fields)))


class _TracerEmitEventOnly:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def emit_event(self, name: str, *, runtime_id: str, **fields: Any) -> None:
        self.events.append((name, runtime_id, dict(fields)))


def _mk_runtime(tracer) -> ExecutionRuntime:
    # registry/loader/resolver в этих тестах не участвуют — можно заглушки.
    return ExecutionRuntime(
        registry=object(),
        loader=object(),
        resolver=object(),
        policy=_DummyPolicy(),
        tracer=tracer,
    )


def test_emit_event_accepts_legacy_payload_and_reserves_runtime_id() -> None:
    tr = _TracerOnRuntimeEvent()
    rt = _mk_runtime(tr)

    # legacy: emit_event(name, payload_dict)
    rt.context.emit_event("plugin.init:test", {"a": 1, "runtime_id": "evil"})

    assert tr.events, "Expected event"
    name, rid, fields = tr.events[-1]
    assert name == "plugin.init:test"
    assert rid == rt.runtime_id
    assert fields.get("a") == 1
    assert "runtime_id" not in fields


def test_emit_event_accepts_kwargs_and_payload_kwarg() -> None:
    tr = _TracerOnRuntimeEvent()
    rt = _mk_runtime(tr)

    rt.context.emit_event(
        "cap.fs.read_bytes",
        payload={"path": "a.txt", "runtime_id": "evil"},
        bytes=5,
        runtime_id="evil2",
    )

    name, rid, fields = tr.events[-1]
    assert name == "cap.fs.read_bytes"
    assert rid == rt.runtime_id
    assert fields["path"] == "a.txt"
    assert fields["bytes"] == 5
    assert "runtime_id" not in fields


def test_emit_event_accepts_name_only() -> None:
    tr = _TracerOnRuntimeEvent()
    rt = _mk_runtime(tr)

    rt.context.emit_event("ping")

    name, rid, fields = tr.events[-1]
    assert name == "ping"
    assert rid == rt.runtime_id
    assert fields == {}


def test_emit_event_fallbacks_to_tracer_emit_event() -> None:
    tr = _TracerEmitEventOnly()
    rt = _mk_runtime(tr)

    rt.context.emit_event("hello", {"x": 1})

    name, rid, fields = tr.events[-1]
    assert name == "hello"
    assert rid == rt.runtime_id
    assert fields["x"] == 1
