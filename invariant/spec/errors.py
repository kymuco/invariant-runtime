# invariant/spec/errors.py
from __future__ import annotations


class PluginError(Exception):
    """
    Base error for plugin system with extra context.
    """

    def __init__(
        self,
        message: str,
        *,
        meta_id: str | None = None,
        cause: BaseException | None = None,
        traceback: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.meta_id = meta_id

        self.cause = cause
        if cause is not None:
            self.__cause__ = cause
            
        self.traceback = traceback

    def __str__(self) -> str:
        parts: list[str] = [self.message]

        if self.meta_id:
            parts.append(f"[plugin={self.meta_id}]")

        if self.cause:
            parts.append(f"cause={self.cause!r}")

        return " ".join(parts)


class PluginNotFoundError(PluginError):
    """Plugin not found."""

class PluginLoadError(PluginError):
    """Plugin failed to load."""

class DependencyCycleError(PluginError):
    """Dependency cycle detected."""

class DependencyNotReady(PluginError):
    """Dependency exists but is not in READY state."""

class AmbiguousPluginReference(PluginError):
    """Plugin reference is ambiguous (e.g. multiple local builds)."""
