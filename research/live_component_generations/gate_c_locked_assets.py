"""Gate C.4: opt-in Windows file-sharing leases for versioned model bundles.

NO copy, no disk registry and no additional runtime router. An open Windows
CreateFileW handle with FILE_SHARE_READ (no WRITE/DELETE share) holds each
declared file against new write/delete opens while the plugin generation is
alive. Cooperative model loaders use host-injected absolute paths.

This is NOT absolute immutability: access through already-existing write
handles, raw-disk writes, privileged interference, mmap/device behavior and
directory ancestor manipulation are outside this pilot. Fail closed on
non-Windows or inaccessible/contended files. Use real OS handles, not chmod.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from catalog_epoch import StalePlan
from gate_c_asset_fence import AssetFence, AssetSeal

MAX_BUNDLE_FILES = 128

if sys.platform == "win32":
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _CreateFileW = _kernel32.CreateFileW
    _CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _CreateFileW.restype = wintypes.HANDLE
    _CloseHandle = _kernel32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL

    _GENERIC_READ = 0x80000000
    _FILE_SHARE_READ = 0x00000001
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_NORMAL = 0x00000080
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def _open_read_lease(path: Path) -> int:
    """Windows sharing denial checked by kernel, not Python file mode."""
    if sys.platform != "win32":
        raise RuntimeError(
            "C.4 no-copy write-exclusion requires Windows CreateFileW"
        )
    raw = _CreateFileW(
        str(path), _GENERIC_READ, _FILE_SHARE_READ, None,
        _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL, None,
    )
    value = int(raw) if raw is not None else 0
    if value in (0, _INVALID_HANDLE_VALUE):
        code = ctypes.get_last_error()
        raise StalePlan(
            f"model file cannot be locked for read-only sharing: "
            f"{path} [winerror={code}]"
        )
    return value


def _close_read_lease(handle: int) -> None:
    if not _CloseHandle(handle):
        code = ctypes.get_last_error()
        raise OSError(code, "CloseHandle(model asset) failed")


@dataclass(slots=True)
class LockedBundle:
    """Owned by existing RealStopFirstHost for a staged/active generation."""

    seal: AssetSeal
    handles: tuple[int, ...]
    closed: bool = False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        failures = []
        for handle in reversed(self.handles):
            try:
                _close_read_lease(handle)
            except OSError as exc:
                failures.append(str(exc))
        if failures:
            raise RuntimeError("failed to close model file leases: " + "; ".join(failures))

    def config(self, prefix: str) -> dict[str, object]:
        if self.closed:
            raise StalePlan("model file leases were already released")
        # One real directory; explicit finite names, without path aliases.
        paths = {entry.path.name: str(entry.path) for entry in self.seal.files}
        parent = str(self.seal.files[0].path.parent)
        hashes = {entry.path.name: entry.sha256 for entry in self.seal.files}
        return {
            prefix + "model_root": parent,
            prefix + "model_paths_json": json.dumps(paths, sort_keys=True),
            prefix + "model_sha256_json": json.dumps(hashes, sort_keys=True),
            prefix + "root_key": self.seal.root_key,
        }


class NoCopyModelGuard:
    """Host-controlled discovery + SHA256 + Windows handle ownership."""

    def __init__(self, fence: AssetFence):
        self.fence = fence

    @staticmethod
    def _validate(seal: AssetSeal) -> None:
        if not 1 <= len(seal.files) <= MAX_BUNDLE_FILES:
            raise StalePlan("model bundle has no files or exceeds file limit")
        dirs = {item.path.parent for item in seal.files}
        if len(dirs) != 1:
            raise StalePlan(
                "C.4 bundle must consist of files in one fixed model directory"
            )
        names = [item.path.name for item in seal.files]
        if len(set(names)) != len(names):
            raise StalePlan("ambiguous model names in bundle")
        # Check all ancestors; do not silently follow symbolic directory
        # aliases between pathname verification and plugin loading.
        directory = seal.files[0].path.parent
        for ancestor in (directory, *directory.parents):
            if ancestor.is_symlink():
                raise StalePlan("symlinked model ancestor is not supported")

    def acquire(self, seal: AssetSeal) -> LockedBundle:
        if sys.platform != "win32":
            raise RuntimeError("Gate C.4 currently supports Windows only")
        self._validate(seal)
        handles = []
        try:
            for item in seal.files:
                handles.append(_open_read_lease(item.path))
            # Hash again AFTER taking every handle. This detects bytes that
            # changed between the initial C.2 stage read and the OS leases.
            self.fence.verify(seal)
        except BaseException:
            for handle in reversed(handles):
                _close_read_lease(handle)
            raise
        return LockedBundle(seal, tuple(handles))

    def verify(self, bundle: LockedBundle) -> None:
        if bundle.closed:
            raise StalePlan("model bundle lease is closed")
        self._validate(bundle.seal)
        self.fence.verify(bundle.seal)
