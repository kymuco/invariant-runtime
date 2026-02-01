# invariant/exec/caps/audit.py
from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Self


@dataclass(frozen=True, slots=True)
class CapAuditEvent:
    name: str
    cap_id: str
    ok: bool
    ts: float
    dt_ms: float
    payload: Mapping[str, Any] | None = None
    error_type: str | None = None
    error: str | None = None


def _truncate(s: str, *, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[: max(0, max_len - 1)] + "…"


def _sanitize(obj: Any, *, max_str: int = 1024, max_items: int = 50, _depth: int = 0) -> Any:
    """
    Make payload JSON-friendly and bounded.
    Containment: never raises.
    """
    try:
        if obj is None or isinstance(obj, (bool, int, float)):
            return obj
        if isinstance(obj, str):
            return _truncate(obj, max_len=max_str)
        if isinstance(obj, bytes):
            return {"bytes_len": len(obj)}
        if isinstance(obj, Mapping):
            out: dict[str, Any] = {}
            i = 0
            for k, v in obj.items():
                if i >= max_items:
                    out["_truncated"] = True
                    break
                ks = str(k)
                out[_truncate(ks, max_len=128)] = _sanitize(v, max_str=max_str, max_items=max_items, _depth=_depth + 1)
                i += 1
            return out
        if isinstance(obj, (list, tuple)):
            out_list: list[Any] = []
            for i, v in enumerate(obj):
                if i >= max_items:
                    out_list.append({"_truncated": True})
                    break
                out_list.append(_sanitize(v, max_str=max_str, max_items=max_items, _depth=_depth + 1))
            return out_list
        return _truncate(str(obj), max_len=max_str)
    except Exception:
        return "<unserializable>"


@dataclass(slots=True)
class _CapAuditSpan:
    audit: CapabilityAudit
    name: str
    cap_id: str
    payload: dict[str, Any]
    _t0: float = 0.0

    def __enter__(self) -> Self:
        self._t0 = time.perf_counter()
        return self

    def add(self, **fields: Any) -> None:
        # containment: never raise
        try:
            for k, v in fields.items():
                if not k:
                    continue
                self.payload[str(k)] = v
        except Exception:
            return

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:
        dt_ms = (time.perf_counter() - self._t0) * 1000.0
        self.audit._emit_event(
            self.name,
            cap_id=self.cap_id,
            ok=(exc is None),
            dt_ms=dt_ms,
            payload=self.payload,
            error=exc if isinstance(exc, BaseException) else None,
        )
        return False  # never swallow


class CapabilityAudit:
    """Capability-level auditing sink. Containment-only (never raises)."""

    def __init__(self, emit: Callable[..., None] | None = None) -> None:
        self._emit = emit

    def span(
        self,
        name: str,
        *,
        cap_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> _CapAuditSpan:
        """
        IMPORTANT CONTRACT:
        - payload here is SNAPSHOT (copied)
        - mutate audit fields ONLY via span.add(...)
        """
        base = dict(payload or {})
        return _CapAuditSpan(self, name=name, cap_id=cap_id, payload=base)

    def _emit_event(
        self,
        name: str,
        *,
        cap_id: str,
        ok: bool,
        dt_ms: float,
        payload: Mapping[str, Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        cb = self._emit
        if cb is None:
            return
        try:
            e = CapAuditEvent(
                name=name,
                cap_id=cap_id,
                ok=ok,
                ts=time.time(),
                dt_ms=float(dt_ms),
                payload=_sanitize(payload) if payload else None,
                error_type=type(error).__name__ if error else None,
                error=_truncate(str(error), max_len=1024) if error else None,
            )
            # emit_event from runtime expects (name, **kwargs)
            cb(
                e.name,
                cap_id=e.cap_id,
                ok=e.ok,
                ts=e.ts,
                dt_ms=round(e.dt_ms, 3),
                payload=e.payload,
                error_type=e.error_type,
                error=e.error,
            )
        except Exception:
            return
