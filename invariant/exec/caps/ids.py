# invariant/exec/caps/ids.py
from __future__ import annotations

from typing import Final

CAP_PREFIX = "invariant:capability."


def cap_base_id(raw: str) -> str:
    """
    Normalize capability reference into base id:
      'invariant:capability.fs_sandbox@1.2.3' -> 'invariant:capability.fs_sandbox'
      'invariant:capability.fs_sandbox'       -> 'invariant:capability.fs_sandbox'
    """
    if not isinstance(raw, str):
        raise TypeError("capability id must be a string")
    s = raw.strip()
    if not s:
        raise ValueError("capability id must be non-empty")
    if "@" in s:
        s = s.rsplit("@", 1)[0].strip()
    return s


# Official ids (no more string-littering)
CAP_INSPECTOR: Final[str]  = f"{CAP_PREFIX}inspector"
CAP_AUDIT: Final[str]      = f"{CAP_PREFIX}audit"

CAP_FS_SANDBOX: Final[str]    = f"{CAP_PREFIX}fs_sandbox"
CAP_WORKSPACE: Final[str]     = f"{CAP_PREFIX}workspace"
CAP_HTTP: Final[str]          = f"{CAP_PREFIX}http"
CAP_KV: Final[str]            = f"{CAP_PREFIX}kv"
CAP_CONFIG_SOURCE: Final[str] = f"{CAP_PREFIX}config_source"

CAP_UNKNOWN: Final[str] = f"{CAP_PREFIX}<unknown>"
