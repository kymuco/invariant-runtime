"""Internal, local-only store of immutable Invariant *plugin artifacts*.

This is not a Python-package manager, remote source, marketplace installer,
public facade, or sandbox. All roots and source ZIPs are supplied explicitly
by the host. InstalledSource presents verified objects to the existing
PluginLoader/PluginService; ManifestSource is reused for manifest validation.

Installed artifacts are content-addressed; identity is exact plugin ID@version.
Logical removal never deletes files that a running consumer may still need.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import re
import shutil
import stat
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator

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


@dataclass
class _LeaseControl:
    keep_on_exit: bool = False


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


def _catalog_fingerprint(state: dict) -> str:
    """Content-based catalog snapshot identity for approved install plans."""
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class LocalPluginStore:
    """Internal store; ZIPs and pinned roots are always supplied locally.

    Managed Store sessions use durable cross-process consumer leases. Callers
    using bare plugin_roots() paths remain untracked: physical cleanup needs
    an explicit host confirmation. Callers must not mutate installed files.
    This class is not part of invariant.api/spec.
    """

    def __init__(self, home: Path) -> None:
        self.home = Path(home).resolve()
        self.objects = self.home / "objects"
        self.staging = self.home / ".staging"
        self.leases = self.home / ".leases"
        self.catalog = self.home / "catalog.json"
        for path in (self.home, self.objects, self.staging, self.leases):
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
                expected_sha256: dict[str, str] | None = None,
                expected_catalog_sha256: str | None = None) -> dict:
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
            if expected_catalog_sha256 is not None and _catalog_fingerprint(current) != expected_catalog_sha256:
                raise PluginStoreError("plugin Store catalog changed since plan approval")
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

    def install_zip(self, archive: Path, *, expected_sha256: str | None = None,
                    expected_ref: str | None = None) -> dict:
        """Explicitly install a single local plugin ZIP as a root.

        The archive's strict manifest supplies the concrete plugin identity.
        Already-installed plugin dependencies are reused from the same Store;
        missing required dependencies are not downloaded or auto-installed.
        For a new dependency, the host must provide/install its ZIP separately.
        This internal convenience method delegates validation and publication
        to install(), without defining a second extraction path.
        """
        archive = Path(archive)
        candidate = _candidate(archive, expected_sha256)
        if expected_ref is not None and _reference(expected_ref) != candidate.ref:
            raise PluginStoreError("downloaded plugin identity does not match requested ref")
        digests = {archive.name: expected_sha256} if expected_sha256 is not None else None
        return self.install([archive], roots=[candidate.ref], expected_sha256=digests)

    def reuse_verified_archive(self, ref: str, expected_sha256: str) -> dict | None:
        """Reuse only an intact installed object with the exact ZIP digest.

        Exact ID/version alone does not authorize skipping a download: the
        stored receipt must record the independently trusted archive SHA-256.
        A hit also pins the requested ref as an installation root atomically.
        Unknown/mismatching digests return None; corruption raises.
        """
        ref = _reference(ref)
        if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256.lower()):
            raise PluginStoreError("expected archive SHA-256 must be 64 hex characters")
        with self._lock:
            state = self._read_state()
            digest = state["plugins"].get(ref)
            if digest is None:
                return None
            self._verify_object(digest)
            # Object integrity alone cannot prove that the catalog maps this
            # requested ID/version to the correct plugin manifest.
            manifest = json.loads((self.objects / digest / "manifest.json").read_text(encoding="utf-8"))
            if _reference(f"{manifest['id']}@{manifest['version']}") != ref:
                raise PluginStoreError("catalog plugin identity mismatch")
            receipt = json.loads((self.objects / digest / _RECEIPT).read_text(encoding="utf-8"))
            if receipt.get("archive_sha256") != expected_sha256.lower():
                return None
            if ref in state["roots"]:
                # Fast path: verify the selected object's bytes, not every
                # unrelated installed plugin merely to skip a network fetch.
                return {"new_objects": [], "installed": list(state["plugins"]), "reused": True}
            manifests = self._manifests(state["plugins"])
            roots = set(state["roots"]) | {ref}
            reachable = self._closure(roots, manifests)
            updated = {
                "schema": 1,
                "roots": sorted(roots),
                "plugins": {key: state["plugins"][key] for key in sorted(reachable)},
            }
            if updated != state:
                _write_json_atomic(self.catalog, updated)
            return {"new_objects": [], "installed": list(updated["plugins"]), "reused": True}

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

    @contextmanager
    def leased_plugin_roots(self, *, _control: _LeaseControl | None = None) -> Iterator[list[Path]]:
        """Hold a durable consumer lease for paths during an entire host session.

        A crashed process leaves a lease behind and blocks reclamation until
        an administrator has separately verified that consumer is gone.
        Merely calling plugin_roots() does NOT acquire a lease.
        """
        token = secrets.token_hex(16)
        lease = self.leases / f"{token}.json"
        with self._lock:
            state = self._read_state()
            self._manifests(state["plugins"])
            digests = sorted(set(state["plugins"].values()))
            file_handle = os.open(lease, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(file_handle, "w", encoding="utf-8") as stream:
                    json.dump({"schema": 1, "digests": digests}, stream, sort_keys=True)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                lease.unlink(missing_ok=True)
                raise
            paths = [self.objects / digest for digest in digests]
        try:
            yield paths
        finally:
            with self._lock:
                if _control is None or not _control.keep_on_exit:
                    lease.unlink(missing_ok=True)

    @contextmanager
    def open_service(
        self,
        *,
        include_unstable: bool = False,
        fail_fast: bool = True,
        tracer: object | None = None,
    ) -> Iterator["PluginService"]:
        """Use a normal PluginService while this Store pins its plugin files.

        The lease precedes discovery, survives until after service.close(),
        and is released if construction or plugin activation fails. Managed
        plugins are shut down in reverse activation order before release.
        Failed shutdown preserves the lease for operator recovery. Hosts must
        close execution contexts before leaving this block. This cannot
        account for a service deliberately used after the block exits or for
        other callers using unleased plugin_roots().
        """
        from invariant.api import PluginService
        from invariant.load.sources.installed import InstalledSource

        control = _LeaseControl()
        with self.leased_plugin_roots(_control=control) as paths:
            if not paths:
                raise ValueError("search_paths must contain at least one path")
            # A lease protects this exact snapshot even if another process
            # changes the catalog before installed manifest discovery.
            service = PluginService._from_sources(
                [InstalledSource(self, paths)],
                include_unstable=include_unstable,
                fail_fast=fail_fast,
                tracer=tracer,
            )
            try:
                yield service
            finally:
                try:
                    # PluginService.close() deliberately leaves managed
                    # instances in the registry. This Store owns this service,
                    # so explicitly stop them in reverse activation order.
                    runtime = service._managed_runtime
                    for ref in reversed(tuple(runtime._initialization_order)):
                        service.shutdown(ref)
                    service.close()
                except BaseException:
                    # A failed stop cannot justify reclaiming plugin files.
                    # Keep the durable lease for explicit operator recovery.
                    control.keep_on_exit = True
                    raise

    def _pinned_digests(self) -> set[str]:
        """Unknown/corrupt leases fail closed; none expire automatically."""
        pinned: set[str] = set()
        for lease in self.leases.iterdir():
            if (lease.is_symlink() or not lease.is_file()
                    or not lease.name.endswith(".json")):
                raise PluginStoreError("invalid consumer lease entry")
            try:
                data = json.loads(lease.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise PluginStoreError("unreadable consumer lease") from exc
            if (not isinstance(data, dict) or data.get("schema") != 1
                    or not isinstance(data.get("digests"), list)
                    or any(not isinstance(v, str) or not _SHA256.fullmatch(v)
                           for v in data["digests"])):
                raise PluginStoreError("invalid consumer lease data")
            pinned.update(data["digests"])
        return pinned

    def prune_unreferenced(self, *, confirmed_untracked_consumers_stopped: bool = False) -> list[str]:
        """Manual-only physical GC; never infer safety from catalog state alone.

        The caller must independently confirm that no host is using bare
        plugin_roots() paths without a lease. Active leases always prevent
        deletion regardless of this confirmation. No TTL or PID heuristics.
        """
        if not confirmed_untracked_consumers_stopped:
            raise PluginStoreError("untracked consumers must be confirmed stopped before pruning")
        with self._lock:
            state = self._read_state()
            keep = set(state["plugins"].values()) | self._pinned_digests()
            removable: list[Path] = []
            for candidate in self.objects.iterdir():
                if (candidate.is_symlink() or not candidate.is_dir()
                        or not _SHA256.fullmatch(candidate.name)):
                    raise PluginStoreError("unexpected object directory entry; refusing pruning")
                if candidate.name not in keep:
                    self._verify_object(candidate.name)
                    removable.append(candidate)
            # Preflight *all* orphaned objects before mutating permissions:
            # chmod on a multiply-linked file would also change its external
            # hardlink target, even though the bytes match our receipt.
            for candidate in removable:
                for child in candidate.rglob("*"):
                    if child.is_file() and not child.is_symlink() and child.stat().st_nlink != 1:
                        raise PluginStoreError("hardlinked object file; refusing pruning")
            for candidate in removable:
                # Installed artifacts are read-only. Windows needs writable file
                # attributes before rmtree; no objects are removed while pinned.
                for child in candidate.rglob("*"):
                    if child.is_file() and not child.is_symlink():
                        child.chmod(stat.S_IREAD | stat.S_IWRITE)
                shutil.rmtree(candidate)
            return [candidate.name for candidate in removable]
