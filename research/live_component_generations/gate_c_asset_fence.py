"""Gate C.2: explicit model-file SHA256 pins, research only.

Only host-declared files are covered. The declaration must include EVERY
model weight/config/tokenizer artifact needed for this experiment. Never
interpret these pins as atomic snapshots, immutable filesystem handles, or
protection against post-verification edits by an adversarial process.
No GPU memory, scheduler, catalog or worker ownership here.
"""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from catalog_epoch import StalePlan


@dataclass(frozen=True, slots=True)
class AssetFile:
    path: Path
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class AssetSeal:
    root_key: str
    files: tuple[AssetFile, ...]


class AssetFence:
    """Pin exact declared on-disk asset bytes before exclusive teardown.

    Locator is controlled by host configuration, not by model/plugin code.
    A stale/missing/replaced asset blocks upgrade BEFORE v1 shutdown.
    Hashing is streamed with bounded RAM even for multi-GB weights.
    """

    def __init__(self, paths_for: Callable[[str], Iterable[Path]]):
        if not callable(paths_for):
            raise TypeError("host asset path locator must be callable")
        self._paths_for = paths_for

    @staticmethod
    def _read(path: Path) -> AssetFile:
        if path.is_symlink():
            raise StalePlan(f"symbolic-link model asset forbidden: {path}")
        try:
            before = path.stat()
            if not stat.S_ISREG(before.st_mode):
                raise StalePlan(f"model asset is not a regular file: {path}")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while block := stream.read(4 * 1024 * 1024):
                    digest.update(block)
            after = path.stat()
        except OSError as exc:
            raise StalePlan(f"model asset unreadable: {path}") from exc
        if (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ino != after.st_ino
        ):
            raise StalePlan(f"model asset changed during hashing: {path}")
        return AssetFile(path.resolve(), before.st_size, digest.hexdigest())

    def pin(self, root_key: str) -> AssetSeal:
        if not isinstance(root_key, str) or "@" not in root_key:
            raise ValueError("exact root version required for model assets")
        declared = tuple(Path(path) for path in self._paths_for(root_key))
        if not 1 <= len(declared) <= 128:
            raise StalePlan("host must declare 1..128 model asset files")
        # Reject duplicate paths, including aliases that resolve alike.
        canonical = tuple(path.resolve() for path in declared)
        if len(set(canonical)) != len(canonical):
            raise StalePlan("duplicate model asset declaration")
        files = tuple(sorted(
            (self._read(path) for path in declared),
            key=lambda item: str(item.path),
        ))
        return AssetSeal(root_key=root_key, files=files)

    def verify(self, seal: AssetSeal) -> None:
        fresh = self.pin(seal.root_key)
        if fresh != seal:
            raise StalePlan(
                f"model assets changed after staging for {seal.root_key}"
            )
