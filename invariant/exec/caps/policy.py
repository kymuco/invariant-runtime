# invariant/exec/caps/policy.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from invariant.exec.caps.ids import (
    CAP_CONFIG_SOURCE,
    CAP_FS_SANDBOX,
    CAP_HTTP,
    CAP_INSPECTOR,
    CAP_KV,
    CAP_WORKSPACE,
)
from invariant.spec.modes import InstanceMode


class CapabilityViewRules(TypedDict):
    allow: set[str] | None
    deny: set[str]
    deny_prefixes: tuple[str, ...]
    reason: str


def _read_limit(limits: object | None, key: str, default: Any) -> Any:
    """
    Read limit from:
      - limits.<key>
      - limits[key] if mapping
    Containment: never raises.
    """
    if limits is None:
        return default
    try:
        if isinstance(limits, Mapping):
            return limits.get(key, default)
        return getattr(limits, key, default)
    except Exception:
        return default


def _read_bool(limits: object | None, key: str, default: bool) -> bool:
    v = _read_limit(limits, key, default)
    try:
        return bool(v)
    except Exception:
        return default


@dataclass(frozen=True, slots=True)
class CapabilityPolicyContext:
    instance_mode: InstanceMode | None
    policy_name: str | None = None
    limits: object | None = None  # <-- важно: policy.limits или dict


class CapabilityPolicy:
    """
    Translates runtime policy context into CapabilityView rules.

    Scope:
    - visibility gating (what plugins can obtain via dependency resolution)
    Not scope:
    - per-operation permissions (FS write/delete, http allowlist, kv limits) —
      these are enforced by capability implementations themselves.
    """

    _MANAGED_REASON = (
        "capability is not available in MANAGED runtime. "
        "Use execution scope: service.execution(...) / service.workspace(...)."
    )
    _LIMITS_REASON = "capability is not available under current policy limits"

    def build_view_rules(self, *, ctx: CapabilityPolicyContext) -> CapabilityViewRules:
        mode = ctx.instance_mode
        limits = ctx.limits

        # Default: allow everything (no allowlist), no explicit deny.
        allow: set[str] | None = None
        deny: set[str] = set()
        deny_prefixes: tuple[str, ...] = ()

        # 1) MANAGED runtime must stay sterile.
        if mode == InstanceMode.MANAGED:
            # Introspection-only. (Config source can be allowed too, but you don't
            # currently register it in managed provider, so no point exposing it.)
            # Keep managed runtime sterile: allow only safe introspection/config.
            allow = {CAP_INSPECTOR, CAP_CONFIG_SOURCE}
            return {
                "allow": allow,
                "deny": deny,
                "deny_prefixes": deny_prefixes,
                "reason": self._MANAGED_REASON,
            }

        # 2) EXECUTION / TRANSIENT / others: gate by limits (defense-in-depth).

        # Global FS kill-switch:
        if not _read_bool(limits, "allow_fs", True):
            deny.update({CAP_FS_SANDBOX, CAP_WORKSPACE, CAP_KV})

        # Network is opt-in.
        if not _read_bool(limits, "allow_network", False):
            deny.add(CAP_HTTP)

        # KV is opt-in (default True in твоём коде, но пусть policy тоже держит оборону).
        if not _read_bool(limits, "allow_kv", True):
            deny.add(CAP_KV)
        # KV needs write-capable FS. If FS writes are disabled, KV becomes a trap.
        if not _read_bool(limits, "allow_fs_write", False):
            deny.add(CAP_KV)

        # Config source is usually safe; but пусть выключается при желании.
        if not _read_bool(limits, "allow_config", True):
            deny.add(CAP_CONFIG_SOURCE)

        reason = self._LIMITS_REASON if deny else "capability gate"
        return {
            "allow": allow,
            "deny": deny,
            "deny_prefixes": deny_prefixes,
            "reason": reason,
        }
