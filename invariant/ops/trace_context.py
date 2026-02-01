# invariant/ops/trace_context.py
from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

_current_execution_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "invariant_current_execution_id", default=None
)

def get_current_execution_id() -> str | None:
    return _current_execution_id.get()

@contextmanager
def bind_execution(execution_id: str) -> Iterator[None]:
    token = _current_execution_id.set(execution_id)
    try:
        yield
    finally:
        _current_execution_id.reset(token)
