"""Internal, local-only store of immutable Invariant *plugin artifacts*.

This is not a Python-package manager, remote source, marketplace installer,
public facade, or sandbox. All roots and source ZIPs are supplied explicitly
by the host. The existing ManifestSource/PluginService remains the consumer.

Installed artifacts are content-addressed; identity is exact plugin ID@version.
Logical removal never deletes files that a running consumer may still need.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from invariant.load.env.lock import FileLock
from invariant.load.sources.manifest import ManifestSource
from invariant.spec.ids import canonical_id_or_ref, canonical_plugin_id, split_id_version
from invariant.spec.versioning import canon_version_key

_MAX_ARCHIVE_BYTES = 30 * 1024 * 1024
_MAX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
_MAX_ENTRIES = 500
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_RECEIPT = ".invariant-receipt.json"


class PluginStoreError(ValueError):
    """Invalid local plugin artifact or inconsistent store state."""


@dataclass(frozen=True)
class _Candidate:
    archive: Path
    ref: str
    content_sha: str
    archive_sha: str
    manifest: dict
    files: tuple[tuple[str, str], ...]


def _reference(ref: str) -> str:
    if not isinstance(ref, str):
        raise PluginStoreError("plugin reference must be a string")
    try:
        name, version = split_id_version(ref)
        if version is None or version.casefold() == "latest":
            raise PluginStoreError("installation requires exact plugin versions")
        return f"{canonical_plugin_id(name)}@{canon_version_key(version)}"
    except PluginStoreError:
        raise
    except Exception as exc:
        raise PluginStoreError(f"invalid plugin reference {ref!r}: {exc}") from exc


def _dependencies(manifest: dict) -> tuple[str, ...]:
    dependencies = manifest.get("dependencies") or []
    if not isinstance(dependencies, list):
        raise PluginStoreError("manifest dependencies must be a list")
    result: list[str] = []
    for dependency in dependencies:
        if not isinstance(dependency, dict) or not isinstance(dependency.get("id"), str):
            raise PluginStoreError("invalid plugin dependency")
        optional = dependency.get("optional", False)
        if not isinstance(optional, bool):
            raise PluginStoreError("plugin dependency optional must be boolean")
        # Normalize using the same ID contract as PluginMeta/DependencySpec.
        try:
            normalized = canonical_id_or_ref(dependency["id"])
        except (TypeError, ValueError) as exc:
            raise PluginStoreError(f"invalid plugin dependency: {exc}") from exc
        # Capabilities belong to the host; optional plugin deps need no install.
        if normalized.startswith("invariant:capability.") or optional:
            continue
        result.append(_reference(normalized))
    return tuple(result)


def _safe_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name or name.startswith("/"):
        raise PluginStoreError(f"unsafe ZIP member: {name!r}")
    normalized = name.rstrip("/")
    parts = PurePosixPath(normalized).parts
    if not parts or any(part in {".", ".."} or ":" in part for part in parts):
        raise PluginStoreError(f"unsafe ZIP member: {name!r}")
    if str(PurePosixPath(normalized)) != normalized:
        raise PluginStoreError(f"noncanonical ZIP member: {name!r}")
    return normalized


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _content_hash(files: Iterable[tuple[str, str]]) -> str:
    return hashlib.sha256(json.dumps(
        list(files), separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def _candidate(archive: Path, expected_sha256: str | None) -> _Candidate:
    archive = Path(archive)
    if archive.stat().st_size > _MAX_ARCHIVE_BYTES:
        raise PluginStoreError("ZIP archive exceeds size limit")
    archive_sha = _hash(archive)
    if expected_sha256 is not None and archive_sha != expected_sha256.lower():
        raise PluginStoreError("ZIP SHA-256 mismatch")
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        if len(members) > _MAX_ENTRIES:
            raise PluginStoreError("ZIP contains too many entries")
        file_hashes: dict[str, str] = {}
        seen: set[str] = set()
        total = 0
        for member in members:
            name = _safe_name(member.filename)
            if _RECEIPT in (part.casefold() for part in PurePosixPath(name).parts):
                raise PluginStoreError("reserved receipt path in ZIP")
            if name.casefold() in seen:
                raise PluginStoreError(f"duplicate ZIP member: {name}")
            seen.add(name.casefold())
            mode = member.external_attr >> 16
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise PluginStoreError(f"special ZIP member forbidden: {name}")
            if member.is_dir():
                continue
            total += member.file_size
            if total > _MAX_UNCOMPRESSED_BYTES:
                raise PluginStoreError("ZIP exceeds uncompressed size limit")
            file_hashes[name] = hashlib.sha256(package.read(member)).hexdigest()
        if "manifest.json" not in file_hashes:
            raise PluginStoreError("root manifest.json required")
        raw = package.read("manifest.json")
        if len(raw) > 1_000_000:
            raise PluginStoreError("manifest exceeds size limit")
        manifest = json.loads(raw)
        if not isinstance(manifest, dict) or manifest.get("manifest_version") != 1:
            raise PluginStoreError("manifest v1 required")
        if not isinstance(manifest.get("id"), str) or not isinstance(manifest.get("version"), str):
            raise PluginStoreError("manifest needs plugin id and exact version")
        ref = _reference(f"{manifest['id']}@{manifest['version']}")
        source = manifest.get("source")
        if not isinstance(source, dict) or not isinstance(source.get("class_name"), str):
            raise PluginStoreError("manifest source.class_name required")
        code_path = _safe_name(source.get("code_path", ""))
        if code_path not in file_hashes:
            raise PluginStoreError("manifest source.code_path not in ZIP")
        _dependencies(manifest)
        files = tuple(sorted(file_hashes.items()))
        return _Candidate(archive, ref, _content_hash(files), archive_sha, manifest, files)


def _write_json_atomic(dest: Path, value: dict) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".catalog-", dir=dest.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, dest)
    finally:
        Path(tmp).unlink(missing_ok=True)


class LocalPluginStore:
    """Internal store; ZIPs and pinned roots are always supplied locally.

    Do not remove objects automatically: neither PluginService nor the store
    tracks active consumers in other processes. Callers must not mutate the
    installed artifact tree. This class is not part of invariant.api/spec.
    """

    def __init__(self, home: Path) -> None:
        self.home = Path(home).resolve()
        self.objects = self.home / "objects"
        self.staging = self.home / ".staging"
        self.catalog = self.home / "catalog.json"
        for path in (self.home, self.objects, self.staging):
            path.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(self.home / ".store.lock", stale_after_s=3600)

    def _read_state(self) -> dict:
        if not self.catalog.exists():
            return {"schema": 1, "roots": [], "plugins": {}}
        state = json.loads(self.catalog.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("schema") != 1:
            raise PluginStoreError("unknown plugin store catalog schema")
        if not isinstance(state.get("roots"), list) or not isinstance(state.get("plugins"), dict):
            raise PluginStoreError("invalid plugin store catalog")
        if any(not isinstance(k, str) or k != _reference(k) or not isinstance(v, str) or not _SHA256.fullmatch(v)
               for k, v in state["plugins"].items()):
            raise PluginStoreError("invalid plugin store catalog entry")
        if any(not isinstance(k, str) or k != _reference(k) for k in state["roots"]):
            raise PluginStoreError("invalid plugin store roots")
        return state

    def state(self) -> dict:
        with self._lock:
            return self._read_state()

    def _verify_object(self, digest: str) -> None:
        if not _SHA256.fullmatch(digest):
            raise PluginStoreError("invalid object digest")
        root = self.objects / digest
        receipt_path = root / _RECEIPT
        if not receipt_path.is_file() or receipt_path.is_symlink():
            raise PluginStoreError("missing or invalid object receipt")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        files = receipt.get("files")
        if receipt.get("schema") != 1 or receipt.get("content_sha256") != digest or not isinstance(files, list):
            raise PluginStoreError("installed plugin receipt mismatch")
        if _content_hash(files) != digest:
            raise PluginStoreError("installed plugin receipt changed")
        for name, expected in files:
            name = _safe_name(name)
            if not isinstance(expected, str) or not _SHA256.fullmatch(expected):
                raise PluginStoreError("invalid receipt file hash")
            file = root / name
            if not file.is_file() or file.is_symlink() or not file.resolve().is_relative_to(root.resolve()):
                raise PluginStoreError("installed plugin file missing or replaced")
            if _hash(file) != expected:
                raise PluginStoreError(f"installed plugin file modified: {name}")

    def _manifests(self, refs: dict[str, str]) -> dict[str, dict]:
        result = {}
        for ref, digest in refs.items():
            self._verify_object(digest)
            result[ref] = json.loads((self.objects / digest / "manifest.json").read_text(encoding="utf-8"))
            if _reference(f"{result[ref]['id']}@{result[ref]['version']}") != ref:
                raise PluginStoreError("catalog plugin identity mismatch")
        return result

    @staticmethod
    def _closure(roots: set[str], manifests: dict[str, dict]) -> set[str]:
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(ref: str) -> None:
            if ref in visiting:
                raise PluginStoreError(f"plugin dependency cycle: {ref}")
            if ref in visited:
                return
            manifest = manifests.get(ref)
            if manifest is None:
                raise PluginStoreError(f"missing required plugin dependency: {ref}")
            visiting.add(ref)
            for child in _dependencies(manifest):
                visit(child)
            visiting.remove(ref)
            visited.add(ref)

        for ref in sorted(roots):
            visit(ref)
        return visited

    def install(self, archives: Iterable[Path], *, roots: Iterable[str],
                expected_sha256: dict[str, str] | None = None) -> dict:
        """Install only explicit local ZIPs; commit catalog after all objects.

        For a repeat root, accepts no archives if dependencies are installed.
        SHA-256 expectations, if supplied, are keyed by ZIP filename.
        """
        expected_sha256 = expected_sha256 or {}
        candidates: dict[str, _Candidate] = {}
        for archive in archives:
            archive = Path(archive)
            candidate = _candidate(archive, expected_sha256.get(archive.name))
            previous = candidates.get(candidate.ref)
            if previous and previous.content_sha != candidate.content_sha:
                raise PluginStoreError(f"different artifacts claim {candidate.ref}")
            candidates[candidate.ref] = candidate
        roots = {_reference(ref) for ref in roots}
        with self._lock:
            current = self._read_state()
            merged = dict(current["plugins"])
            for ref, candidate in candidates.items():
                if ref in merged and merged[ref] != candidate.content_sha:
                    raise PluginStoreError(f"plugin ID/version collision: {ref}")
                merged[ref] = candidate.content_sha
            manifests = self._manifests(current["plugins"])
            manifests.update({ref: candidate.manifest for ref, candidate in candidates.items()})
            all_roots = set(current["roots"]) | roots
            reachable = self._closure(all_roots, manifests)
            newly_installed: list[str] = []
            for ref in sorted(reachable):
                candidate = candidates.get(ref)
                if candidate is None:
                    continue
                dest = self.objects / candidate.content_sha
                if dest.exists():
                    self._verify_object(candidate.content_sha)
                    continue
                staging = Path(tempfile.mkdtemp(prefix="plugin-", dir=self.staging))
                try:
                    with zipfile.ZipFile(candidate.archive) as package:
                        names = tuple(sorted(_safe_name(member.filename) for member in package.infolist()
                                             if not member.is_dir()))
                        if names != tuple(name for name, _ in candidate.files):
                            raise PluginStoreError("ZIP changed during installation")
                        for member in package.infolist():
                            name = _safe_name(member.filename)
                            target = staging / name
                            if member.is_dir():
                                target.mkdir(parents=True, exist_ok=True)
                            else:
                                target.parent.mkdir(parents=True, exist_ok=True)
                                with package.open(member) as source, target.open("wb") as sink:
                                    shutil.copyfileobj(source, sink)
                    for name, expected in candidate.files:
                        if _hash(staging / name) != expected:
                            raise PluginStoreError(f"ZIP contents changed: {name}")
                    # Reuse current strict manifest checks; no plugin imports or activation.
                    descriptor = ManifestSource([staging])._read_manifest(staging / "manifest.json", dev_mode=False)
                    actual_ref = _reference(f"{descriptor.meta.id}@{descriptor.meta.version}")
                    if actual_ref != candidate.ref:
                        raise PluginStoreError("manifest identity changed during installation")
                    receipt = {"schema": 1, "content_sha256": candidate.content_sha,
                               "archive_sha256": candidate.archive_sha,
                               "files": list(candidate.files)}
                    (staging / _RECEIPT).write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")
                    for path in staging.rglob("*"):
                        if path.is_file():
                            path.chmod(0o444)
                    staging.rename(dest)
                    newly_installed.append(ref)
                finally:
                    if staging.exists():
                        shutil.rmtree(staging, ignore_errors=True)
            new_state = {"schema": 1, "roots": sorted(all_roots),
                         "plugins": {ref: merged[ref] for ref in sorted(reachable)}}
            _write_json_atomic(self.catalog, new_state)
            return {"new_objects": newly_installed, "installed": list(new_state["plugins"])}

    def plugin_roots(self) -> list[Path]:
        """Paths for the existing PluginService.from_manifest_paths interface."""
        with self._lock:
            state = self._read_state()
            self._manifests(state["plugins"])
            return [self.objects / digest for digest in sorted(set(state["plugins"].values()))]

    def uninstall_root(self, ref: str) -> dict:
        """Remove a requested root from the catalog; NEVER delete object bytes."""
        ref = _reference(ref)
        with self._lock:
            state = self._read_state()
            if ref not in state["roots"]:
                raise PluginStoreError(f"not an installed root: {ref}")
            remaining = set(state["roots"]) - {ref}
            reachable = self._closure(remaining, self._manifests(state["plugins"]))
            state = {"schema": 1, "roots": sorted(remaining),
                     "plugins": {r: state["plugins"][r] for r in sorted(reachable)}}
            _write_json_atomic(self.catalog, state)
            return state
