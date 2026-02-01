# invariant/spec/versioning.py
from __future__ import annotations

from packaging.version import InvalidVersion, Version


def canon_version_key(ver: str) -> str:
    """
    Canonical semver-like key for version matching/dedup.
    Pads release to 3 segments so '1.0' == '1.0.0'.
    Preserves pre/post/dev/local segments.
    """
    if not isinstance(ver, str) or not ver.strip():
        raise ValueError("version must be non-empty string")

    try:
        v = Version(ver.strip())
    except InvalidVersion as e:
        raise ValueError(f"bad version format: {ver!r}") from e

    epoch = f"{v.epoch}!" if v.epoch != 0 else ""

    release = list(v.release)
    if len(release) < 3:
        release.extend([0] * (3 - len(release)))
    release_s = ".".join(str(x) for x in release)

    pre = f"{v.pre[0]}{v.pre[1]}" if v.pre else ""
    post = f".post{v.post}" if v.post is not None else ""
    dev = f".dev{v.dev}" if v.dev is not None else ""
    local = f"+{v.local}" if v.local else ""

    return f"{epoch}{release_s}{pre}{post}{dev}{local}"
