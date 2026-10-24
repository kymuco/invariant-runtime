# invariant/spec/source.py
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class PluginSourceSpec:
    """
    Internal source description.
    Used only by loaders.
    """

    kind: Literal[
        "filesystem",
        "entrypoint",
        "manifest",
        "installed",
        "zip",
        "api",
    ]

    module: str | None = None
    class_name: str | None = None
    path: Path | None = None

    entry_point: str | None = None

    url: str | None = None
    checksum: str | None = None

    extra: Mapping[str, Any] = field(default_factory=dict)


    def __post_init__(self) -> None:
        object.__setattr__(self, "extra", MappingProxyType(dict(self.extra or {})))

        if self.path is not None and not isinstance(self.path, Path):
            raise ValueError("path must be pathlib.Path")
        
        k = self.kind
        if k == "filesystem":
            if self.path is None:
                raise ValueError("filesystem source requires path")
            # module/class_name: either both provided, or both omitted (computed elsewhere)
            if (self.module is None) != (self.class_name is None):
                raise ValueError("filesystem source requires both module and class_name or neither")

        elif k == "entrypoint":
            if not self.entry_point:
                raise ValueError("entrypoint source requires entry_point")

        elif k in {"manifest", "installed"}:
            # depends on your manifest model; keep minimal
            # must have at least module+class_name OR entry_point OR path
            if not (self.entry_point or (self.module and self.class_name) or self.path):
                raise ValueError("manifest source spec is incomplete")

        elif k == "api":
            if not self.url:
                raise ValueError("api source requires url")

        elif k == "zip":
            if self.path is None and not self.url:
                raise ValueError("zip source requires path or url")
            
        else:
            raise ValueError(f"unknown source kind: {k!r}")
