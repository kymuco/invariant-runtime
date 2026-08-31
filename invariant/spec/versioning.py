# invariant/spec/versioning.py
from __future__ import annotations

from invariant.spec._version import InvalidVersion, Version

__all__ = ["InvalidVersion", "Version", "canon_version_key"]


def canon_version_key(ver: str) -> str:
    """
    Canonical concrete-version key for matching and deduplication.

    Release segments are padded to at least three components so ``1``/``1.0``/
    ``1.0.0`` share the same registry key. Pre/post/dev/local components and
    epochs are normalized and preserved.
    """
    if not isinstance(ver, str) or not ver.strip():
        raise ValueError("version must be non-empty string")

    try:
        version = Version(ver.strip())
    except InvalidVersion as exc:
        raise ValueError(f"bad version format: {ver!r}") from exc

    epoch = f"{version.epoch}!" if version.epoch != 0 else ""

    release = list(version.release)
    if len(release) < 3:
        release.extend([0] * (3 - len(release)))
    release_s = ".".join(str(part) for part in release)

    pre = f"{version.pre[0]}{version.pre[1]}" if version.pre else ""
    post = f".post{version.post}" if version.post is not None else ""
    dev = f".dev{version.dev}" if version.dev is not None else ""
    local = f"+{version.local}" if version.local else ""

    return f"{epoch}{release_s}{pre}{post}{dev}{local}"
