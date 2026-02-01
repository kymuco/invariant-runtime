# invariant/ops/errors.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ErrorPayload:
    code: str
    message: str
    detail: str | None = None
    extra: dict[str, Any] | None = None


class RuntimeErrorBase(Exception):
    """
    Base class for runtime-level errors. Meant to be serialized.
    - stable error 'code'
    - optional detail/extra for debugging (still safe for clients)
    """
    code: str = "runtime_error"

    def __init__(self, message: str, *, detail: str | None = None, extra: dict[str, Any] | None = None):
        super().__init__(str(message))
        self.detail = detail
        self.extra = extra or None

    def payload(self) -> ErrorPayload:
        return ErrorPayload(code=self.code, message=str(self), detail=self.detail, extra=self.extra)


class InvalidCommand(RuntimeErrorBase):
    code = "invalid_command"


class ExecutionNotFound(RuntimeErrorBase):
    code = "execution_not_found"


class ExecutionStateError(RuntimeErrorBase):
    code = "execution_state_error"


class PluginHandleNotFound(RuntimeErrorBase):
    code = "plugin_handle_not_found"


class PluginCallError(RuntimeErrorBase):
    code = "plugin_call_error"


class ExecutionLimitExceeded(RuntimeErrorBase):
    code = "execution_limit_exceeded"


class PluginCallTimeout(RuntimeErrorBase):
    code = "timeout"
