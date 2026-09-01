# invariant/exec/caps/fs.py
from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, Self

from invariant.exec.caps.audit import CapabilityAudit
from invariant.spec.capabilities import FilesystemCapabilityError


class FSAccessError(FilesystemCapabilityError):
    pass


class FSPathTraversalBlocked(FSAccessError):
    pass


class FSWriteDenied(FSAccessError):
    pass


class FSDeleteDenied(FSAccessError):
    pass


class FSReadLimitExceeded(FSAccessError):
    pass


class FSWriteLimitExceeded(FSAccessError):
    pass


def _norm_root(root: str | Path) -> Path:
    p = Path(root).expanduser()
    # resolve() may fail if path doesn't exist; create root lazily in ops that need it
    try:
        return p.resolve()
    except Exception:
        return p.absolute()
    
def _reject_symlink_chain(root: Path, target: Path) -> None:
    """
    Best-effort: walk from root to target and reject any existing symlink segments.
    IMPORTANT: this checks the *filesystem objects* that exist, not resolved paths.
    """
    try:
        root = root.resolve(strict=False)
    except Exception:
        root = root.absolute()

    # We only check segments under root.
    try:
        rel = target.relative_to(root)
    except Exception:
        raise FSPathTraversalBlocked("Target escapes root") from None

    cur = root
    for part in rel.parts:
        cur = cur / part
        try:
            # is_symlink() uses lstat and works even for broken symlinks
            if cur.is_symlink():
                raise FSPathTraversalBlocked(f"Symlink is not allowed: {cur}")
        except OSError:
            # If stat fails, treat as suspicious
            raise FSPathTraversalBlocked(f"Symlink check failed for: {cur}") from None


class _NoopSpan:
    def __enter__(self) -> Self:
        return self
    def add(self, **_fields: Any) -> None:
        return
    def __exit__(self, *_exc) -> Literal[False]:
        return False


@dataclass(frozen=True, slots=True)
class FSAccess:
    """
    Executable FS capability:
    - root-scoped paths only (blocks traversal)
    - byte limits for read/write
    - policy flags for write/delete
    - atomic writes
    """

    root: Path
    write: bool = False
    delete: bool = False
    read_limit: int = 2_000_000
    write_limit: int = 2_000_000
    cap_id: str | None = None
    _audit: CapabilityAudit | None = None

    def __post_init__(self) -> None:
        if self.read_limit <= 0:
            raise ValueError("read_limit must be > 0")
        if self.write_limit <= 0:
            raise ValueError("write_limit must be > 0")
        
    def bind_audit(self, audit: CapabilityAudit, *, cap_id: str) -> Self:
        return replace(self, _audit=audit, cap_id=cap_id)

    def _span(self, op: str, payload: Mapping[str, Any] | None = None) -> Any:
        a = self._audit
        if a is None:
            return _NoopSpan()
        return a.span(
            f"cap.fs.{op}",
            cap_id=(self.cap_id or "invariant:capability.<unknown>"),
            payload=payload,
        )

    # ---- internal impls (NO audit, to avoid double events) ----

    def _read_bytes_impl(self, rel: str | Path) -> bytes:
        p = self.resolve(rel)
        limit = int(self.read_limit)
        root = _norm_root(self.root)
        _reject_symlink_chain(root, p)
        try:
            with p.open("rb") as f:
                data = f.read(limit + 1)
        except FileNotFoundError as e:
            raise FSAccessError(f"File not found: {rel}") from e

        if len(data) > limit:
            raise FSReadLimitExceeded(f"Read limit exceeded: > {limit}")
        return data

    def _write_bytes_impl(self, rel: str | Path, data: bytes, *, atomic: bool) -> None:
        if not self.write:
            raise FSWriteDenied("Write denied by policy")
        if len(data) > self.write_limit:
            raise FSWriteLimitExceeded(f"Write limit exceeded: {len(data)} > {self.write_limit}")

        p = self.resolve(rel)
        root = _norm_root(self.root)
        _reject_symlink_chain(root, p.parent)
        if p.exists():
            _reject_symlink_chain(root, p)
        p.parent.mkdir(parents=True, exist_ok=True)

        if not atomic:
            p.write_bytes(data)
            return

        tmp_fd: int | None = None
        tmp_path: str | None = None
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(prefix=".invariant_tmp_", dir=str(p.parent))
            with os.fdopen(tmp_fd, "wb") as f:
                tmp_fd = None
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            last_error: PermissionError | None = None
            for _ in range(10):
                try:
                    os.replace(tmp_path, str(p))
                    tmp_path = None
                    return
                except PermissionError as exc:
                    last_error = exc
                    time.sleep(0.02)
            if last_error is not None:
                raise last_error
        finally:
            if tmp_fd is not None:
                try:
                    os.close(tmp_fd)
                except Exception:
                    pass
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    # ---- permissions ----

    def allow_write(self) -> bool:
        return bool(self.write)

    def allow_delete(self) -> bool:
        return bool(self.delete)

    def max_read_bytes(self) -> int:
        return int(self.read_limit)

    def max_write_bytes(self) -> int:
        return int(self.write_limit)

    # ---- path handling ----

    def resolve(self, rel: str | Path) -> Path:
        """
        Resolve a relative path under root; block traversal.
        Accepts:
          - "file.txt"
          - "dir/sub.txt"
          - Path(...)
        """
        raw = Path(rel)

        # Windows drive-relative paths like "C:foo" are not allowed
        if getattr(raw, "drive", ""):
            raise FSPathTraversalBlocked("Drive paths are not allowed")

        if raw.is_absolute():
            raise FSPathTraversalBlocked("Absolute paths are not allowed")

        # STRICT: reject any '..' present in the ORIGINAL input (before normalization)
        for part in raw.parts:
            if part == "..":
                raise FSPathTraversalBlocked("Path traversal is not allowed")

        # Now normalize (collapses '.' and redundant separators)
        rel_path = Path(os.path.normpath(str(raw)))

        # Defensive: normpath should not reintroduce '..', but keep invariant anyway
        for part in rel_path.parts:
            if part == "..":
                raise FSPathTraversalBlocked("Path traversal is not allowed")
            

        # Ensure root exists? Not here; resolution should be pure.
        root = _norm_root(self.root)
        candidate = (root / rel_path)

        # Containment check via realpath (follows symlinks).
        try:
            cand_resolved = candidate.resolve(strict=False)
        except Exception:
            cand_resolved = candidate.absolute()

        # Check candidate is inside root
        try:
            root_resolved = root.resolve(strict=False)
        except Exception:
            root_resolved = root.absolute()

        if cand_resolved == root_resolved:
            return candidate

        if root_resolved not in cand_resolved.parents:
            raise FSPathTraversalBlocked(f"Path escapes root: {rel_path}")

        return candidate

    # ---- read ----

    def read_bytes(self, rel: str | Path) -> bytes:
        with self._span("read_bytes", {"path": str(rel), "max_read_bytes": int(self.read_limit)}) as sp:
            data = self._read_bytes_impl(rel)
            sp.add(bytes=len(data))
            return data

    def read_text(self, rel: str | Path, *, encoding: str = "utf-8", errors: str = "strict") -> str:
        # Canonical audit event is read_bytes (even for text helpers).
        with self._span(
            "read_bytes",
            {
                "path": str(rel),
                "max_read_bytes": int(self.read_limit),
                "encoding": encoding,
            },
        ) as sp:
            data = self._read_bytes_impl(rel)
            s = data.decode(encoding, errors=errors)
            sp.add(bytes=len(data), chars=len(s))
            return s

    # ---- write ----

    def write_bytes(self, rel: str | Path, data: bytes, *, atomic: bool = True) -> None:
        with self._span(
            "write_bytes",
            {"path": str(rel), "bytes": len(data), "atomic": bool(atomic), "max_write_bytes": int(self.write_limit)},
        ) as sp:
            self._write_bytes_impl(rel, data, atomic=bool(atomic))
            sp.add(wrote_bytes=len(data))

    def write_text(
        self,
        rel: str | Path,
        text: str,
        *,
        encoding: str = "utf-8",
        errors: str = "strict",
        atomic: bool = True,
    ) -> None:
        # Canonical audit event is write_bytes (even for text helpers).
        data = text.encode(encoding, errors=errors)
        with self._span(
            "write_bytes",
            {
                "path": str(rel),
                "bytes": len(data),
                "max_write_bytes": int(self.write_limit),
                "atomic": bool(atomic),
                "encoding": encoding,
                "chars": len(text),
            },
        ) as sp:
            self._write_bytes_impl(rel, data, atomic=bool(atomic))
            sp.add(wrote_bytes=len(data))

    # ---- listing ----

    def list_dir(self, rel: str | Path = ".", *, max_entries: int = 10_000) -> list[str]:
        with self._span("list_dir", {"path": str(rel), "max_entries": int(max_entries)}) as sp:
            p = self.resolve(rel)
            root = _norm_root(self.root)
            _reject_symlink_chain(root, p)

            if not p.exists():
                sp.add(count=0, exists=False)
                return []
            if not p.is_dir():
                raise FSAccessError(f"Not a directory: {rel}")

            out: list[str] = []
            max_n = int(max_entries)
            # IMPORTANT: avoid sorting full directory (can be huge -> memory/time DoS).
            # Order is not guaranteed; caller must not rely on ordering.
            for i, child in enumerate(p.iterdir()):
                if i >= max_n:
                    break
                out.append(child.name)
            sp.add(count=len(out), exists=True, truncated=(len(out) >= max_n))
            return out

    # ---- delete ----

    def delete_file(self, rel: str | Path) -> None:
        with self._span("delete_file", {"path": str(rel)}) as sp:
            if not self.delete:
                raise FSDeleteDenied("Delete denied by policy")
            p = self.resolve(rel)
            root = _norm_root(self.root)
            _reject_symlink_chain(root, p.parent)
            if p.exists():
                _reject_symlink_chain(root, p)

            if p.is_dir():
                raise FSAccessError("delete_file() cannot delete directories")
            try:
                p.unlink()
                sp.add(deleted=True)
            except FileNotFoundError:
                sp.add(deleted=False, missing=True)
                return

    def delete_tree(self, rel: str | Path = ".") -> None:
        """
        Directory deletion is dangerous. Keep it explicit.
        """
        with self._span("delete_tree", {"path": str(rel)}) as sp:
            if not self.delete:
                raise FSDeleteDenied("Delete denied by policy")
            root = self.resolve(rel)
            base = _norm_root(self.root)
            _reject_symlink_chain(base, root)
            if not root.exists():
                sp.add(deleted=False, missing=True)
                return
            if root.is_file():
                root.unlink()
                sp.add(deleted=True, kind="file")
                return

            # Best-effort recursive delete inside root.
            # Do NOT follow symlinks (important).
            root_str = str(root)
            files_deleted = 0
            dirs_deleted = 0
            errors = 0
            for dirpath, dirnames, filenames in os.walk(root_str, topdown=False, followlinks=False):
                for name in filenames:
                    fp = os.path.join(dirpath, name)
                    try:
                        os.unlink(fp)
                        files_deleted += 1
                    except Exception:
                        errors += 1
                for name in dirnames:
                    dp = os.path.join(dirpath, name)
                    try:
                        os.rmdir(dp)
                        dirs_deleted += 1
                    except Exception:
                        errors += 1
            try:
                os.rmdir(root_str)
                dirs_deleted += 1
            except Exception:
                errors += 1

            sp.add(deleted=True, kind="dir", files_deleted=files_deleted, dirs_deleted=dirs_deleted, errors=errors)
