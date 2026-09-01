# invariant/api/types.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True, slots=True)
class PluginInfo:
    """Stable host-facing projection of one discovered plugin build."""

    id: str
    version: str | None
    name: str | None = None
    maturity: str = "stable"
    categories: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkspaceOptions:
    allow_write: bool = True
    allow_delete: bool = False
    max_read_bytes: int = 10_000_000
    max_write_bytes: int = 10_000_000
    root_dirname: str = ".invariant/workspaces"
    
    def __post_init__(self) -> None:
        if self.max_read_bytes <= 0:
            raise ValueError("max_read_bytes must be > 0")
        if self.max_write_bytes <= 0:
            raise ValueError("max_write_bytes must be > 0")

        p = PurePosixPath(self.root_dirname)
        if p.is_absolute():
            raise ValueError("root_dirname must be relative")
        if ".." in p.parts:
            raise ValueError("root_dirname must not contain '..'")