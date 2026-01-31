# invariant/spec/context.py
from __future__ import annotations
from typing import Any, Mapping, Protocol


class EmitEvent(Protocol):
    """
    Backward-compatible event emitter for plugins.
    Supported forms:
      - emit_event(name, payload_dict)
      - emit_event(name, payload=payload_dict)
      - emit_event(name, **fields)
    Must never raise to plugin code (containment is implemented by runtime).
    """
    def __call__(
        self,
        name: str,
        payload: Mapping[str, Any] | None = None,
        **fields: Any,
    ) -> None: ...


class PluginContext(Protocol):
    runtime_id: str
    config: Mapping[str, Any]
    emit_event: EmitEvent | None
