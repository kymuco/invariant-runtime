"""Gate C.3 opt-in single-file pre-copied model snapshot for tiny fixtures.

Research-only trusted-host stage store. Never silently copy huge GGUF /
safetensors weights. No hard links: they alias mutable source bytes.
Snapshot bytes are checked against C.2's AssetSeal before/after copy.
Read-only chmod is cooperative, NOT a protection against malicious writers.
"""
from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from catalog_epoch import StalePlan
from gate_c_asset_fence import AssetFile, AssetSeal

MIB = 1024 * 1024


@dataclass(frozen=True, slots=True)
class Snapshot:
    original: Path
    file: Path
    size: int
    sha256: str
    root_key: str


class SnapshotVault:
    """Bounded disk snapshot. The owner must close this vault on final stop."""

    def __init__(self, *, max_file_bytes: int = 16 * MIB):
        if not 1 <= max_file_bytes <= 64 * MIB:
            raise ValueError("C.3 snapshot file limit must be 1..64 MiB")
        self.max_file_bytes = max_file_bytes
        # C.3 is a tiny fixture, not a persistent content-addressed store.
        # Bound repeated staged-but-abandoned candidate copies too.
        self._max_snapshots = 4
        self._snapshots_created = 0
        self._temp = tempfile.TemporaryDirectory(prefix="invariant-c3-")
        self._closed = False

    @staticmethod
    def _digest_file(path: Path) -> tuple[int, str]:
        try:
            if path.is_symlink():
                raise StalePlan("snapshot path must not be a symbolic link")
            meta = path.stat()
            if not stat.S_ISREG(meta.st_mode):
                raise StalePlan("snapshot path must be a regular file")
            h = hashlib.sha256()
            size = 0
            with path.open("rb") as reader:
                for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                    size += len(chunk)
                    h.update(chunk)
            return size, h.hexdigest()
        except OSError as exc:
            raise StalePlan(f"snapshot cannot be verified: {path}") from exc

    def create(self, seal: AssetSeal) -> Snapshot:
        if self._closed:
            raise RuntimeError("snapshot vault is closed")
        if self._snapshots_created >= self._max_snapshots:
            raise StalePlan("C.3 staged snapshot budget exhausted")
        if len(seal.files) != 1:
            raise StalePlan(
                "C.3 explicitly supports one model artifact per root; "
                "multi-shard assets require a different immutable store"
            )
        original: AssetFile = seal.files[0]
        if original.size > self.max_file_bytes:
            raise StalePlan(
                f"C.3 model snapshot exceeds explicit limit: "
                f"{original.size} > {self.max_file_bytes} bytes"
            )
        if original.path.is_symlink():
            raise StalePlan("model artifact symlink not supported")
        # Random, owner-private directory. No hardlinks (mutable aliases).
        folder = Path(tempfile.mkdtemp(prefix="asset-", dir=self._temp.name))
        output = folder / "model.bin"
        size = 0
        h = hashlib.sha256()
        try:
            with original.path.open("rb") as src, output.open("xb") as dst:
                while part := src.read(1024 * 1024):
                    size += len(part)
                    if size > self.max_file_bytes:
                        raise StalePlan("model asset grew beyond snapshot limit")
                    dst.write(part)
                    h.update(part)
                dst.flush()
                os.fsync(dst.fileno())
            if (size, h.hexdigest()) != (original.size, original.sha256):
                raise StalePlan("model bytes changed while staging snapshot")
            source_size, source_digest = self._digest_file(original.path)
            if (source_size, source_digest) != (original.size, original.sha256):
                raise StalePlan("model source changed during snapshot creation")
            output.chmod(0o444)
            snap = Snapshot(
                original=original.path, file=output,
                size=size, sha256=h.hexdigest(), root_key=seal.root_key,
            )
            self.verify(snap)
            self._snapshots_created += 1
            return snap
        except BaseException:
            try:
                output.chmod(0o666)
                output.unlink(missing_ok=True)
                folder.rmdir()
            except OSError:
                pass
            raise

    def verify(self, snapshot: Snapshot) -> None:
        if self._closed:
            raise RuntimeError("snapshot vault is closed")
        size, digest = self._digest_file(snapshot.file)
        if (size, digest) != (snapshot.size, snapshot.sha256):
            raise StalePlan(f"staged model snapshot altered: {snapshot.root_key}")

    def close(self):
        if self._closed:
            return
        # Windows read-only files must be made writable for TempDirectory
        # cleanup on some Python versions.
        for path in Path(self._temp.name).rglob("model.bin"):
            try:
                path.chmod(0o666)
            except OSError:
                pass
        self._temp.cleanup()
        self._closed = True
