# invariant/api/policies.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any

from invariant.exec.runtime.store import (
    EphemeralInstanceStore,
    ManagedInstanceStore,
)
from invariant.spec.modes import InstanceMode


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    # ---- coarse global gates (capability visibility) ----
    allow_fs: bool = True
    allow_config: bool = True

    allow_fs_write: bool = False
    allow_fs_delete: bool = False
    max_read_bytes: int = 2_000_000
    max_write_bytes: int = 2_000_000

    # Workspace can have its own limits (policy decides).
    workspace_max_read_bytes: int = 10_000_000
    workspace_max_write_bytes: int = 10_000_000
    workspace_root_dirname: str = ".invariant/workspaces"

    # --- new: network/process gates ---
    allow_network: bool = False
    http_allow_hosts: tuple[str, ...] = ()
    http_timeout_s: float = 10.0
    http_max_bytes: int = 2_000_000
    http_allow_any_host: bool = False
    http_allow_redirects: bool = False
    http_allow_private_ips: bool = False

    allow_kv: bool = True
    kv_max_bytes: int = 1_000_000

    # ConfigSource: env access is dangerous — off by default.
    allow_env_config: bool = False
    config_env_prefix: str = "INVARIANT_"

    def __post_init__(self) -> None:
        if self.max_read_bytes <= 0:
            raise ValueError("max_read_bytes must be > 0")
        if self.max_write_bytes <= 0:
            raise ValueError("max_write_bytes must be > 0")
        if self.workspace_max_read_bytes <= 0:
            raise ValueError("workspace_max_read_bytes must be > 0")
        if self.workspace_max_write_bytes <= 0:
            raise ValueError("workspace_max_write_bytes must be > 0")
        if self.http_max_bytes <= 0:
            raise ValueError("http_max_bytes must be > 0")
        if self.kv_max_bytes <= 0:
            raise ValueError("kv_max_bytes must be > 0")
        # workspace_root_dirname must be relative and traversal-safe
        p = PurePosixPath(self.workspace_root_dirname)
        if p.is_absolute():
            raise ValueError("workspace_root_dirname must be relative")
        if ".." in p.parts:
            raise ValueError("workspace_root_dirname must not contain '..'")
        if self.allow_env_config and (not isinstance(self.config_env_prefix, str) or not self.config_env_prefix):
            raise ValueError("config_env_prefix must be non-empty when allow_env_config=True")


class BasePolicy:
    instance_mode: InstanceMode

    def __init__(
        self,
        *,
        limits: SandboxLimits | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        self.limits = limits or SandboxLimits()
        if not isinstance(self.limits, SandboxLimits):
            raise TypeError("limits must be SandboxLimits")
        self.config = MappingProxyType(dict(config or {}))

    def create_store(self, *, registry):
        raise NotImplementedError

    def __repr__(self) -> str:
        # Important: stable repr (do NOT include per-execution data)
        return f"<{self.__class__.__name__}>"


class ManagedPolicy(BasePolicy):
    instance_mode = InstanceMode.MANAGED

    def create_store(self, *, registry):
        return ManagedInstanceStore(registry=registry)


class EphemeralPolicy(BasePolicy):
    instance_mode = InstanceMode.EPHEMERAL

    def create_store(self, *, registry):
        return EphemeralInstanceStore()