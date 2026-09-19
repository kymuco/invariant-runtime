from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import fields
from typing import Any, Literal

from invariant.api.policies import EphemeralPolicy, SandboxLimits

PolicyProfile = Literal["dev", "stage", "prod"]
_VALID_PROFILES = {"dev", "stage", "prod"}
_DEFAULT_PROFILE: PolicyProfile = "dev"


def profile_from_env(*, env_var: str = "INVARIANT_PROFILE", default: str = _DEFAULT_PROFILE) -> PolicyProfile:
    raw = os.getenv(env_var, default)
    return normalize_profile(raw, default=default)


def normalize_profile(profile: str | None, *, default: str = _DEFAULT_PROFILE) -> PolicyProfile:
    val = (profile or default).strip().lower()
    if val in _VALID_PROFILES:
        return val  # type: ignore[return-value]
    allowed = ", ".join(sorted(_VALID_PROFILES))
    raise ValueError(f"Unknown policy profile {profile!r}. Allowed: {allowed}")


def sandbox_limits_for_profile(
    profile: str | None,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> SandboxLimits:
    p = normalize_profile(profile)
    base = _base_limits_for_profile(p)
    if not overrides:
        return base
    return _merge_limits(base, overrides)


def ephemeral_policy_for_profile(
    profile: str | None,
    *,
    config: Mapping[str, Any] | None = None,
    limits_overrides: Mapping[str, Any] | None = None,
) -> EphemeralPolicy:
    limits = sandbox_limits_for_profile(profile, overrides=limits_overrides)
    return EphemeralPolicy(limits=limits, config=config)


def _base_limits_for_profile(profile: PolicyProfile) -> SandboxLimits:
    if profile == "dev":
        return SandboxLimits(
            allow_fs=True,
            allow_fs_write=True,
            allow_fs_delete=True,
            max_read_bytes=10_000_000,
            max_write_bytes=10_000_000,
            workspace_max_read_bytes=20_000_000,
            workspace_max_write_bytes=20_000_000,
            allow_network=False,
            allow_process=True,
            process_max_timeout_s=120.0,
            process_max_output_chars=40_000,
            allow_kv=True,
            kv_max_bytes=2_000_000,
            allow_env_config=True,
            config_env_prefix="INVARIANT_",
        )

    if profile == "stage":
        return SandboxLimits(
            allow_fs=True,
            allow_fs_write=True,
            allow_fs_delete=False,
            max_read_bytes=4_000_000,
            max_write_bytes=4_000_000,
            workspace_max_read_bytes=10_000_000,
            workspace_max_write_bytes=10_000_000,
            allow_network=False,
            allow_kv=True,
            kv_max_bytes=1_000_000,
            allow_env_config=False,
            config_env_prefix="INVARIANT_",
        )

    return SandboxLimits(
        allow_fs=True,
        allow_fs_write=True,
        allow_fs_delete=False,
        max_read_bytes=2_000_000,
        max_write_bytes=2_000_000,
        workspace_max_read_bytes=6_000_000,
        workspace_max_write_bytes=6_000_000,
        allow_network=False,
        allow_kv=True,
        kv_max_bytes=1_000_000,
        allow_env_config=False,
        config_env_prefix="INVARIANT_",
    )


def _merge_limits(base: SandboxLimits, overrides: Mapping[str, Any]) -> SandboxLimits:
    allowed = {f.name for f in fields(SandboxLimits)}
    data = {f.name: getattr(base, f.name) for f in fields(SandboxLimits)}
    for k, v in overrides.items():
        if k not in allowed:
            keys = ", ".join(sorted(allowed))
            raise ValueError(f"Unknown SandboxLimits override key {k!r}. Allowed keys: {keys}")
        if k == "http_allow_hosts":
            if isinstance(v, list):
                data[k] = tuple(str(x) for x in v)
                continue
            if isinstance(v, tuple):
                data[k] = tuple(str(x) for x in v)
                continue
        data[k] = v
    return SandboxLimits(**data)
