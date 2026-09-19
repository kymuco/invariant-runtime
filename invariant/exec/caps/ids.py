# invariant/exec/caps/ids.py
from __future__ import annotations

from typing import Final

from invariant.spec.capabilities import (
    CAPABILITY_PREFIX,
    CONFIG_SOURCE_CAPABILITY,
    FS_SANDBOX_CAPABILITY,
    HTTP_CAPABILITY,
    INSPECTOR_CAPABILITY,
    KEY_VALUE_CAPABILITY,
    PROCESS_CAPABILITY,
    WORKSPACE_CAPABILITY,
)

# Compatibility shim for runtime internals. Canonical plugin-facing capability
# identities live in invariant.spec.capabilities.
CAP_PREFIX: Final[str] = CAPABILITY_PREFIX


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


CAP_INSPECTOR: Final[str] = INSPECTOR_CAPABILITY.id
CAP_FS_SANDBOX: Final[str] = FS_SANDBOX_CAPABILITY.id
CAP_WORKSPACE: Final[str] = WORKSPACE_CAPABILITY.id
CAP_HTTP: Final[str] = HTTP_CAPABILITY.id
CAP_KV: Final[str] = KEY_VALUE_CAPABILITY.id
CAP_PROCESS: Final[str] = PROCESS_CAPABILITY.id
CAP_CONFIG_SOURCE: Final[str] = CONFIG_SOURCE_CAPABILITY.id

# Internal-only runtime capabilities are intentionally not part of the public
# plugin-author contract.
CAP_AUDIT: Final[str] = f"{CAP_PREFIX}audit"
CAP_UNKNOWN: Final[str] = f"{CAP_PREFIX}<unknown>"
