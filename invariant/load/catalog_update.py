"""Host-pinned, offline catalog update admission with persistent rollback checks.

A host independently authenticates the exact metadata SHA-256 and names the
expected publisher/channel. This is NOT publisher signature verification, a
trusted clock, catalog fetching, or a marketplace. The checkpoint is local
state under the host's filesystem protection, not a tamper-proof ledger.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from invariant.load.plugin_catalog import (
    PluginCatalogError,
    VerifiedPluginCatalog,
    _no_nonfinite,
    _unique_fields,
)
from invariant.load.plugin_store import _write_json_atomic

_SHA = re.compile(r"[0-9a-fA-F]{64}\Z")
_NAME = re.compile(r"[a-z][a-z0-9._-]{0,63}\Z")
_MAX_METADATA_BYTES = 4096
_MAX_VALIDITY = timedelta(days=30)
_FIELDS = frozenset({
    "schema_version", "publisher", "channel", "sequence",
    "previous_catalog_sha256", "catalog_sha256", "issued_at", "expires_at",
})
_STATE_FIELDS = frozenset({
    "schema_version", "publisher", "channel", "sequence", "metadata_sha256",
    "catalog_sha256", "expires_at", "observed_at",
})


class CatalogUpdateError(ValueError):
    """Untrusted metadata, replay/rollback, invalid clock or damaged checkpoint."""


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise CatalogUpdateError(f"{label} must be 64 hexadecimal digits")
    return value.lower()


def _timestamp(raw: object, label: str) -> datetime:
    if not isinstance(raw, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", raw):
        raise CatalogUpdateError(f"{label} must be a UTC YYYY-MM-DDTHH:MM:SSZ timestamp")
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise CatalogUpdateError(f"invalid {label}") from exc


def _now(value: datetime | None) -> datetime:
    if value is None:
        value = datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CatalogUpdateError("a timezone-aware trusted host clock is required")
    return value.astimezone(timezone.utc)


def _utc_string(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_json(payload: bytes, label: str) -> dict:
    try:
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_unique_fields,
            parse_constant=_no_nonfinite,
        )
    except (ValueError, UnicodeError) as exc:
        raise CatalogUpdateError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise CatalogUpdateError(f"invalid {label} object")
    return value


class _OsCheckpointLock:
    """Persistent OS-backed lock: no stale unlink, no shared ownership token.

    OS file locks are released by the kernel when a process dies. The lock
    file itself is deliberately not removed; replacing it breaks inode-based
    locking on POSIX. An in-process mutex also protects one instance's fd.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._thread = threading.Lock()
        self._fd: int | None = None

    def __enter__(self):
        self._thread.acquire()
        fd = None
        try:
            if self.path.is_symlink():
                raise CatalogUpdateError("catalog checkpoint lock must not be a symlink")
            fd = os.open(
                self.path,
                os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            if os.name == "nt":
                import msvcrt

                # msvcrt.locking locks one byte starting at the current offset.
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX)
            self._fd = fd
            return self
        except BaseException:
            if fd is not None:
                os.close(fd)
            self._thread.release()
            raise

    def __exit__(self, exc_type, exc, tb):
        fd = self._fd
        assert fd is not None
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
            self._fd = None
            self._thread.release()


def _sync_directory(path: Path) -> None:
    """Make completed rename/directory creation durable on POSIX filesystems.

    Windows Python does not provide a portable directory-fsync interface;
    OS/process-crash recovery is supported there but power-loss durability
    requires an external host guarantee for the checkpoint volume.
    """
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True, slots=True)
class AcceptedCatalogUpdate:
    """A verified catalog admitted for a specific local publisher/channel."""

    catalog: VerifiedPluginCatalog
    metadata_sha256: str
    sequence: int
    publisher: str
    channel: str
    expires_at: datetime


class CatalogUpdateJournal:
    """Host-scoped monotonic checkpoint independent of plugin Store catalogs.

    The journal must reside in a host-protected directory and remain durable.
    Removing or restoring it defeats rollback resistance. The publisher name
    is *pinned by the host*, not authenticated by anything in the JSON alone.
    """

    def __init__(self, home: Path, *, publisher: str, channel: str):
        if not isinstance(publisher, str) or not _NAME.fullmatch(publisher):
            raise CatalogUpdateError("invalid host-pinned publisher ID")
        if not isinstance(channel, str) or not _NAME.fullmatch(channel):
            raise CatalogUpdateError("invalid host-pinned channel ID")
        self.home = Path(home).resolve()
        self.publisher = publisher
        self.channel = channel
        key = hashlib.sha256(f"{publisher}\0{channel}".encode("ascii")).hexdigest()
        self.checkpoint = self.home / f"catalog-update-{key}.json"
        self._lock = _OsCheckpointLock(self.home / f".catalog-update-{key}.lock")

    def _read(self) -> dict | None:
        if self.checkpoint.is_symlink():
            raise CatalogUpdateError("catalog update checkpoint must not be a symlink")
        if not self.checkpoint.exists():
            return None
        if not self.checkpoint.is_file() or self.checkpoint.stat().st_size > _MAX_METADATA_BYTES:
            raise CatalogUpdateError("invalid catalog update checkpoint file")
        state = _parse_json(self.checkpoint.read_bytes(), "catalog update checkpoint")
        if state.keys() != _STATE_FIELDS or type(state["schema_version"]) is not int or state["schema_version"] != 1:
            raise CatalogUpdateError("invalid catalog update checkpoint schema")
        if state["publisher"] != self.publisher or state["channel"] != self.channel:
            raise CatalogUpdateError("catalog update checkpoint identity mismatch")
        if type(state["sequence"]) is not int or state["sequence"] < 1:
            raise CatalogUpdateError("invalid catalog update checkpoint sequence")
        _sha(state["metadata_sha256"], "checkpoint metadata SHA-256")
        _sha(state["catalog_sha256"], "checkpoint catalog SHA-256")
        _timestamp(state["expires_at"], "checkpoint expires_at")
        _timestamp(state["observed_at"], "checkpoint observed_at")
        return state

    def admit(
        self,
        metadata: bytes,
        catalog_bytes: bytes,
        *,
        trusted_metadata_sha256: str,
        now: datetime | None = None,
        bootstrap: bool = False,
    ) -> AcceptedCatalogUpdate:
        """Verify externally pinned update and persist a monotonic checkpoint.

        New channels require explicit bootstrap of sequence 1. Later updates
        must advance exactly one sequence and link to the previous catalog
        digest. Identical, unexpired metadata may be replayed idempotently.
        """
        if not isinstance(metadata, bytes) or not 0 < len(metadata) <= _MAX_METADATA_BYTES:
            raise CatalogUpdateError("catalog update metadata must be 1..4096 bytes")
        trusted_hash = _sha(trusted_metadata_sha256, "trusted metadata SHA-256")
        actual = hashlib.sha256(metadata).hexdigest()
        if not hmac.compare_digest(actual, trusted_hash):
            raise CatalogUpdateError("catalog update metadata SHA-256 mismatch")
        doc = _parse_json(metadata, "catalog update metadata")
        if doc.keys() != _FIELDS or type(doc["schema_version"]) is not int or doc["schema_version"] != 1:
            raise CatalogUpdateError("unsupported catalog update metadata schema")
        if doc["publisher"] != self.publisher or doc["channel"] != self.channel:
            raise CatalogUpdateError("catalog publisher/channel does not match host pin")
        sequence = doc["sequence"]
        if type(sequence) is not int or sequence < 1:
            raise CatalogUpdateError("catalog update sequence must be a positive integer")
        new_digest = _sha(doc["catalog_sha256"], "catalog SHA-256")
        previous = doc["previous_catalog_sha256"]
        if previous is not None:
            previous = _sha(previous, "previous catalog SHA-256")
        issued = _timestamp(doc["issued_at"], "issued_at")
        expires = _timestamp(doc["expires_at"], "expires_at")
        if expires <= issued or expires - issued > _MAX_VALIDITY:
            raise CatalogUpdateError("catalog metadata validity must be positive and at most 30 days")
        # Reuse the existing strict, digest-bound catalog parser; an untrusted
        # metadata envelope can never introduce a second catalog schema.
        try:
            catalog = VerifiedPluginCatalog.from_bytes(catalog_bytes, expected_sha256=new_digest)
        except PluginCatalogError as exc:
            raise CatalogUpdateError(f"catalog bytes failed verification: {exc}") from exc
        accepted = AcceptedCatalogUpdate(catalog, trusted_hash, sequence, self.publisher, self.channel, expires)

        created_home = not self.home.exists()
        # The host supplies an existing parent/volume for this protected state.
        self.home.mkdir(parents=False, exist_ok=True)
        if created_home:
            _sync_directory(self.home.parent)
        with self._lock:
            # Clock freshness is checked *after* waiting for any other writer.
            now = _now(now)
            if issued > now or expires <= now:
                raise CatalogUpdateError("catalog metadata not yet valid or expired")
            state = self._read()
            if state is None:
                if not bootstrap or sequence != 1 or previous is not None:
                    raise CatalogUpdateError("first catalog requires explicit sequence-1 bootstrap")
            else:
                if bootstrap:
                    raise CatalogUpdateError("cannot bootstrap an existing catalog channel")
                if now < _timestamp(state["observed_at"], "observed_at"):
                    raise CatalogUpdateError("host clock moved backwards since catalog admission")
                if sequence == state["sequence"]:
                    if state["metadata_sha256"] != trusted_hash or state["catalog_sha256"] != new_digest:
                        raise CatalogUpdateError("same catalog sequence has different content")
                elif sequence == state["sequence"] + 1:
                    if previous != state["catalog_sha256"]:
                        raise CatalogUpdateError("catalog chain does not match prior accepted digest")
                else:
                    raise CatalogUpdateError("catalog rollback, replay, or skipped sequence")
            _write_json_atomic(self.checkpoint, {
                "schema_version": 1,
                "publisher": self.publisher,
                "channel": self.channel,
                "sequence": sequence,
                "metadata_sha256": trusted_hash,
                "catalog_sha256": new_digest,
                "expires_at": _utc_string(expires),
                "observed_at": _utc_string(now),
            })
            # _write_json_atomic fsyncs the temp file before os.replace;
            # sync the containing directory as well before acknowledging it.
            _sync_directory(self.home)
        return accepted

    def assert_current(self, accepted: AcceptedCatalogUpdate, *, now: datetime | None = None) -> None:
        """Recheck that an admitted catalog is current and still unexpired.

        Hosts should call this before planning AND before applying a plan.
        This is a point-in-time check, not a lock across network operations.
        """
        if not isinstance(accepted, AcceptedCatalogUpdate):
            raise CatalogUpdateError("expected an accepted catalog update")
        with self._lock:
            now = _now(now)  # Sample default host clock after lock contention.
            state = self._read()
            if state is None:
                raise CatalogUpdateError("no catalog update checkpoint")
            if now < _timestamp(state["observed_at"], "observed_at"):
                raise CatalogUpdateError("host clock moved backwards since catalog admission")
            if now >= _timestamp(state["expires_at"], "expires_at"):
                raise CatalogUpdateError("catalog update expired")
            if (accepted.publisher, accepted.channel, accepted.sequence,
                    accepted.metadata_sha256, accepted.catalog.sha256) != (
                self.publisher, self.channel, state["sequence"],
                state["metadata_sha256"], state["catalog_sha256"],
            ):
                raise CatalogUpdateError("catalog is no longer the current admitted update")
