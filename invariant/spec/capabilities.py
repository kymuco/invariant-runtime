from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from invariant.spec.versioning import canon_version_key


CAPABILITY_PREFIX = "invariant:capability."


@dataclass(frozen=True, slots=True)
class CapabilityRef:
    """Versioned reference to a runtime-owned capability contract."""

    id: str
    version: str

    def __post_init__(self) -> None:
        cap_id = str(self.id).strip()
        suffix = cap_id[len(CAPABILITY_PREFIX):] if cap_id.startswith(CAPABILITY_PREFIX) else ""
        if not suffix or any(ch.isspace() for ch in suffix):
            raise ValueError(f"Invalid capability id: {self.id!r}")
        if "@" in cap_id:
            raise ValueError("CapabilityRef.id must be a base id without @version")

        version = canon_version_key(self.version)
        object.__setattr__(self, "id", cap_id)
        object.__setattr__(self, "version", version)

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    def __str__(self) -> str:
        return self.ref


INSPECTOR_CAPABILITY = CapabilityRef(f"{CAPABILITY_PREFIX}inspector", "0.1.0")
FS_SANDBOX_CAPABILITY = CapabilityRef(f"{CAPABILITY_PREFIX}fs_sandbox", "0.3.0")
WORKSPACE_CAPABILITY = CapabilityRef(f"{CAPABILITY_PREFIX}workspace", "0.3.0")
HTTP_CAPABILITY = CapabilityRef(f"{CAPABILITY_PREFIX}http", "0.1.0")
KEY_VALUE_CAPABILITY = CapabilityRef(f"{CAPABILITY_PREFIX}kv", "0.1.0")
PROCESS_CAPABILITY = CapabilityRef(f"{CAPABILITY_PREFIX}process", "0.1.0")
CONFIG_SOURCE_CAPABILITY = CapabilityRef(f"{CAPABILITY_PREFIX}config_source", "0.1.0")

BUILTIN_CAPABILITIES: tuple[CapabilityRef, ...] = (
    CONFIG_SOURCE_CAPABILITY,
    FS_SANDBOX_CAPABILITY,
    HTTP_CAPABILITY,
    INSPECTOR_CAPABILITY,
    KEY_VALUE_CAPABILITY,
    PROCESS_CAPABILITY,
    WORKSPACE_CAPABILITY,
)


class CapabilityOperationError(RuntimeError):
    """Base class for failures after a capability handle has been granted."""


class FilesystemCapabilityError(CapabilityOperationError):
    """A filesystem capability operation failed."""


class HttpCapabilityError(CapabilityOperationError):
    """An HTTP capability operation failed."""


class KeyValueCapabilityError(CapabilityOperationError):
    """A key-value capability operation failed."""


class ProcessCapabilityError(CapabilityOperationError):
    """A process capability operation failed."""


@runtime_checkable
class FilesystemCapability(Protocol):
    """Plugin-facing contract for sandbox or workspace filesystem access."""

    def allow_write(self) -> bool: ...

    def allow_delete(self) -> bool: ...

    def max_read_bytes(self) -> int: ...

    def max_write_bytes(self) -> int: ...

    def read_bytes(self, rel: str | Path) -> bytes: ...

    def read_text(
        self,
        rel: str | Path,
        *,
        encoding: str = "utf-8",
        errors: str = "strict",
    ) -> str: ...

    def write_bytes(self, rel: str | Path, data: bytes, *, atomic: bool = True) -> None: ...

    def write_text(
        self,
        rel: str | Path,
        text: str,
        *,
        encoding: str = "utf-8",
        errors: str = "strict",
        atomic: bool = True,
    ) -> None: ...

    def mkdir(
        self,
        rel: str | Path,
        *,
        parents: bool = True,
        exist_ok: bool = True,
    ) -> bool: ...

    def list_dir(self, rel: str | Path = ".", *, max_entries: int = 10_000) -> list[str]: ...

    def delete_file(self, rel: str | Path) -> None: ...

    def delete_tree(self, rel: str | Path = ".") -> None: ...


@runtime_checkable
class HttpCapability(Protocol):
    """Plugin-facing governed HTTP(S) client contract."""

    def get_bytes(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> bytes: ...

    def get_text(
        self,
        url: str,
        *,
        encoding: str = "utf-8",
        errors: str = "strict",
    ) -> str: ...

    def get_json(self, url: str) -> Any: ...

    def post_json(
        self,
        url: str,
        payload: Any,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Any: ...


@runtime_checkable
class KeyValueCapability(Protocol):
    """Plugin-facing persistent key-value contract."""

    def get(self, key: str, default: Any = None) -> Any: ...

    def set(self, key: str, value: Any) -> None: ...

    def delete(self, key: str) -> bool: ...

    def keys(self) -> list[str]: ...

    def list_keys(self, prefix: str = "") -> list[str]: ...


@runtime_checkable
class ProcessCapability(Protocol):
    """Workspace-bound synchronous process execution contract."""

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path = ".",
        timeout_s: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class ConfigSourceCapability(Protocol):
    """Plugin-facing read-only configuration contract."""

    def get(self, key: str) -> Any: ...

    def list_keys(self, prefix: str = "") -> Iterable[str]: ...


@runtime_checkable
class RuntimeInspectorCapability(Protocol):
    """Plugin-facing read-only runtime inspection contract."""

    def health(self) -> dict[str, Any]: ...

    def environment(self) -> dict[str, Any]: ...

    def list_descriptors(self) -> list[dict[str, Any]]: ...

    def list_instances(self) -> list[dict[str, Any]]: ...

    def dependency_graph(self) -> dict[str, dict[str, list[str]]]: ...

    def get_state(self, plugin_id: str) -> dict[str, Any]: ...


__all__ = [
    "BUILTIN_CAPABILITIES",
    "CONFIG_SOURCE_CAPABILITY",
    "CapabilityOperationError",
    "CapabilityRef",
    "ConfigSourceCapability",
    "FS_SANDBOX_CAPABILITY",
    "FilesystemCapability",
    "FilesystemCapabilityError",
    "HTTP_CAPABILITY",
    "HttpCapability",
    "HttpCapabilityError",
    "INSPECTOR_CAPABILITY",
    "KEY_VALUE_CAPABILITY",
    "KeyValueCapability",
    "KeyValueCapabilityError",
    "PROCESS_CAPABILITY",
    "ProcessCapability",
    "ProcessCapabilityError",
    "RuntimeInspectorCapability",
    "WORKSPACE_CAPABILITY",
]
