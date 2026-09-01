# invariant/exec/caps/kv.py
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any, Literal, Self

from invariant.exec.caps.audit import CapabilityAudit
from invariant.exec.caps.fs import FSAccess
from invariant.spec.capabilities import KeyValueCapabilityError


class KVError(KeyValueCapabilityError):
    pass


class KVCorruptError(KVError):
    pass


_MISSING = object()


def _validate_rel_path(rel_path: str) -> str:
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise ValueError("rel_path must be non-empty string")
    p = PurePosixPath(rel_path)
    if p.is_absolute():
        raise ValueError("rel_path must be relative")
    if ".." in p.parts:
        raise ValueError("rel_path must not contain '..'")
    return str(p)


class _NoopSpan:
    """No-audit span with the same interface as _CapAuditSpan."""
    def __enter__(self) -> Self:
        return self

    def add(self, **fields: Any) -> None:
        return

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:
        return False


class KVStore:
    """
    JSON-backed KV over FSAccess.

    Audit contract (Variant B):
    - span() receives a SNAPSHOT payload
    - any fields discovered during operation must be added via span.add(...)
    - values are never logged
    """

    __slots__ = ("_fs", "_rel_path", "_repair_on_write", "_max_bytes", "_audit", "_cap_id")

    def __init__(
        self,
        *,
        fs: FSAccess,
        rel_path: str = "kv.json",
        repair_on_write: bool = True,
        max_bytes: int = 1_000_000,
    ) -> None:
        if not isinstance(fs, FSAccess):
            raise TypeError("fs must be FSAccess")
        self._fs = fs
        self._rel_path = _validate_rel_path(rel_path)
        self._repair_on_write = bool(repair_on_write)
        mb = int(max_bytes)
        if mb <= 0:
            raise ValueError("max_bytes must be > 0")
        self._max_bytes = mb

        self._audit: CapabilityAudit | None = None
        self._cap_id: str | None = None

    def bind_audit(self, audit: CapabilityAudit, *, cap_id: str) -> Self:
        if not isinstance(audit, CapabilityAudit):
            raise TypeError("audit must be CapabilityAudit")
        if not isinstance(cap_id, str) or not cap_id.strip():
            raise ValueError("cap_id must be non-empty string")

        cls = type(self)
        v = cls(
            fs=self._fs,
            rel_path=self._rel_path,
            repair_on_write=self._repair_on_write,
            max_bytes=self._max_bytes,
        )
        v._audit = audit
        v._cap_id = cap_id
        return v

    # ---------------- public API ----------------

    def get(self, key: str, default: Any = None) -> Any:
        with self._span("get", payload={"key": key}) as sp:
            data = self._load()
            val = data.get(key, _MISSING)
            hit = val is not _MISSING
            sp.add(hit=hit)
            if hit:
                sp.add(value_type=type(val).__name__)
                return val
            return default

    def set(self, key: str, value: Any) -> None:
        with self._span("set", payload={"key": key, "value_type": type(value).__name__}) as sp:
            recovered = False
            try:
                data = self._load()
            except KVCorruptError:
                if not self._repair_on_write:
                    raise
                data = {}
                recovered = True

            if recovered:
                sp.add(recovered=True)

            data[key] = value
            self._save(data)

    def delete(self, key: str) -> bool:
        with self._span("delete", payload={"key": key}) as sp:
            recovered = False
            try:
                data = self._load()
            except KVCorruptError:
                if not self._repair_on_write:
                    raise
                data = {}
                recovered = True

            if recovered:
                sp.add(recovered=True)

            existed = key in data
            sp.add(existed=existed)

            if existed:
                data.pop(key, None)
                self._save(data)

            return existed

    def keys(self) -> list[str]:
        # kept for API stability
        return self.list_keys(prefix="")

    def list_keys(self, prefix: str = "") -> list[str]:
        # backward-compatible API (old code used list_keys)
        if prefix is None:
            prefix = ""
        if not isinstance(prefix, str):
            raise TypeError("prefix must be str")
        with self._span("list_keys", payload={"prefix": prefix}) as sp:
            data = self._load()
            out = sorted([k for k in data.keys() if (not prefix or k.startswith(prefix))])
            sp.add(count=len(out))
            return out

    # ---------------- internals ----------------

    def _span(self, op: str, payload: Mapping[str, Any] | None = None):
        if self._audit is None or self._cap_id is None:
            return _NoopSpan()
        return self._audit.span(f"cap.kv.{op}", cap_id=self._cap_id, payload=payload)

    def _load(self) -> dict[str, Any]:
        with self._span("load", payload={"path": self._rel_path}) as sp:
            p = self._fs.resolve(self._rel_path)
            if not p.exists():
                sp.add(exists=False, count=0)
                return {}

            sp.add(exists=True)

            # Read as bytes to enforce KVStore max_bytes independently of FS limits.
            b = self._fs.read_bytes(self._rel_path)
            sp.add(bytes=len(b))
            if len(b) > self._max_bytes:
                raise KVError(f"KV store too large: {len(b)} > max_bytes {self._max_bytes}")
            raw = b.decode("utf-8", errors="strict")

            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as e:
                raise KVCorruptError(f"KV file is not valid JSON: {self._rel_path}") from e

            if not isinstance(obj, dict):
                raise KVCorruptError(f"KV root must be a JSON object: {self._rel_path}")

            for k in obj.keys():
                if not isinstance(k, str):
                    raise KVCorruptError(f"KV keys must be strings: {self._rel_path}")

            sp.add(count=len(obj))
            return obj

    def _save(self, data: dict[str, Any]) -> None:
        with self._span("save", payload={"path": self._rel_path, "count": len(data)}) as sp:
            text = json.dumps(
                data,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            raw = text.encode("utf-8", errors="strict")
            sp.add(bytes=len(raw))
            if len(raw) > self._max_bytes:
                raise KVError(f"KV store exceeds max_bytes: {len(raw)} > {self._max_bytes}")
            self._fs.write_bytes(self._rel_path, raw, atomic=True)
