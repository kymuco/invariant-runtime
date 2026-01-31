# invariant/spec/ids.py
from __future__ import annotations

import re
from functools import lru_cache
from packaging.version import Version, InvalidVersion

_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._:-]*[a-z0-9])?$", re.IGNORECASE)


@lru_cache(maxsize=4096)
def canonical_plugin_id(value: str) -> str:
    """
    Canonicalize and validate plugin id.
    - trims
    - lowercases
    - validates against allowed charset
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("plugin id must be non-empty string")
    cid = value.strip().lower()
    if not _ID_RE.match(cid):
        raise ValueError(f"plugin id has unsupported format: {value!r}")
    return cid

def split_id_version(raw: str) -> tuple[str, str | None]:
    if "@" not in raw:
        return raw, None
    base, ver = raw.rsplit("@", 1)
    base = base.strip()
    ver = ver.strip() or None
    return base, ver

def canonical_id_or_ref(raw: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("id/ref must be non-empty string")
    base, ver = split_id_version(raw.strip())
    base = canonical_plugin_id(base)
    if ver is None:
        return base
    try:
        Version(ver)
    except InvalidVersion as e:
        raise ValueError(f"bad version format: {ver!r}") from e
    return f"{base}@{ver}"

def base_id(raw: str) -> str:
    """
    Return canonical base plugin id without version suffix.
    Accepts both 'id' and 'id@version'.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("id/ref must be non-empty string")
    base, _ = split_id_version(raw.strip())
    return canonical_plugin_id(base)

