# invariant/ops/trace_context.py
from __future__ import annotations

from contextlib import contextmanager
import contextvars
from typing import Iterator, Optional

_current_execution_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "invariant_current_execution_id", default=None
)

def get_current_execution_id() -> Optional[str]:
    return _current_execution_id.get()

@contextmanager
def bind_execution(execution_id: str) -> Iterator[None]:
    token = _current_execution_id.set(execution_id)
    try:
        yield
    finally:
        _current_execution_id.reset(token)
