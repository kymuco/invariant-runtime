# invariant/exec/caps/catalog.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from invariant.exec.caps.ids import (
    CAP_AUDIT,
    CAP_CONFIG_SOURCE,
    CAP_FS_SANDBOX,
    CAP_HTTP,
    CAP_INSPECTOR,
    CAP_KV,
    CAP_PREFIX,
    CAP_WORKSPACE,
)
from invariant.spec.versioning import canon_version_key


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    """
    Platform contract for a capability.

    This is the canonical, investor-grade list of what the platform provides.
    Runtime registration MUST match this catalog in strict mode.
    """

    cap_id: str                 # base id (no @version)
    version: str                # semver string (platform contract version)
    title: str
    description: str = ""

    # Event namespace prefix (for audits)
    events_prefix: str | None = None

    # Limits exposed by the capability (bytes/time/hosts/etc)
    limits: Mapping[str, Any] = field(default_factory=dict)

    # Extra notes (policy, security posture, etc)
    notes: str | None = None


CATALOG: dict[str, CapabilitySpec] = {
    CAP_INSPECTOR: CapabilitySpec(
        cap_id=CAP_INSPECTOR,
        version="0.1.0",
        title="Runtime inspector",
        description="Safe dict-only view over runtime state and selected environment info.",
        events_prefix=None,
        notes="Typically allowed even in managed runtime.",
    ),
    CAP_AUDIT: CapabilitySpec(
        cap_id=CAP_AUDIT,
        version="1.0.0",
        title="Capability audit sink",
        description="Structured capability audit events (containment-only).",
        events_prefix="cap.",
    ),
    CAP_FS_SANDBOX: CapabilitySpec(
        cap_id=CAP_FS_SANDBOX,
        version="0.2.0",
        title="FS sandbox",
        description="Root-scoped file access with traversal and symlink hardening, read/write limits, atomic writes.",
        events_prefix="cap.fs.",
        limits={"read_limit": "bytes", "write_limit": "bytes"},
        notes="Denied in managed policy by default (safety).",
    ),
    CAP_WORKSPACE: CapabilitySpec(
        cap_id=CAP_WORKSPACE,
        version="0.2.0",
        title="Workspace FS",
        description="Persistent workspace-scoped FS access (policy gated).",
        events_prefix="cap.fs.",
        limits={"read_limit": "bytes", "write_limit": "bytes"},
    ),
    CAP_HTTP: CapabilitySpec(
        cap_id=CAP_HTTP,
        version="0.1.0",
        title="HTTP client",
        description="Outbound HTTP(S) client with host allowlist, timeouts, max response size.",
        events_prefix="cap.http.",
        limits={"timeout_s": "seconds", "max_bytes": "bytes", "allow_hosts": "list"},
        notes="HTTPS-only by default. Query/fragment are not logged.",
    ),
    CAP_KV: CapabilitySpec(
        cap_id=CAP_KV,
        version="0.1.0",
        title="KV store",
        description="JSON-backed KV store with corruption detection and optional repair-on-write.",
        events_prefix="cap.kv.",
        limits={"file_max_bytes": "bytes"},
    ),
    CAP_CONFIG_SOURCE: CapabilitySpec(
        cap_id=CAP_CONFIG_SOURCE,
        version="0.1.0",
        title="Config source",
        description="Read-only config source (env + dict) exposed to plugins via context/capabilities.",
        events_prefix=None,
        notes="Safe; typically allowed everywhere.",
    ),
}

# ---------------- Single source of truth for "official list" ----------------
#
# Important invariant:
# - OFFICIAL_CAPS / ALL_CAPS are derived from CATALOG only (no duplication).
#
OFFICIAL_CAPS: Final[frozenset[str]] = frozenset(CATALOG.keys())
# Preserve insertion order from the dict literal (platform-defined, stable).
ALL_CAPS: Final[tuple[str, ...]] = tuple(sorted(OFFICIAL_CAPS))


def _validate_catalog() -> None:
    """
    Hard fail on contract inconsistencies early (import time).
    If this breaks, the platform contract is broken.
    """
    for cap_id, spec in CATALOG.items():
        if cap_id != spec.cap_id:
            raise RuntimeError(f"CATALOG key mismatch: key={cap_id!r} spec.cap_id={spec.cap_id!r}")
        if not isinstance(cap_id, str) or not cap_id.startswith(CAP_PREFIX):
            raise RuntimeError(f"Invalid capability id in catalog: {cap_id!r}")
        # Ensure version is valid/normalizable (semver-ish).
        try:
            canon_version_key(spec.version)
        except Exception as e:
            raise RuntimeError(f"Invalid capability version in catalog: {cap_id} version={spec.version!r}") from e


_validate_catalog()
