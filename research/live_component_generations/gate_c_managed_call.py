"""Gate C.1: one opt-in bounded method-call port, research ONLY.

No production PluginService edits. This does NOT supersede legacy get().
One fixed root and method, no raw managed plugin reference exposed.
Existing B.1/B.2 hosts remain the sole generation/catalog/lease owners.
A returned str or bytes is detached from the plugin lifetime; deferred
results, generators, mutable objects and background tasks are unsupported.

This port is a convenience boundary for a trusted host, NOT a security
sandbox, a capability-granting policy or a general-purpose RPC protocol.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

from gate_b_exclusive_host import RealStopFirstHost
from gate_b_leased_host import LeasedReadinessHost


class DeferredOrMutableResult(TypeError):
    """A managed call returned a value that may outlive its generation."""


class ManagedCallPort:
    """Opt-in, single-root, synchronous call + management of ONE existing host.

    There is NO extra route pointer, registry, instance cache or scheduler
    here. Gate B's chosen host handles admissions, leases, CAS, resource
    checks and lifecycle. The port does not hand out the underlying plugin.
    """

    __slots__ = ("__host",)

    def __init__(self, host: LeasedReadinessHost | RealStopFirstHost):
        if not isinstance(host, (LeasedReadinessHost, RealStopFirstHost)):
            raise TypeError("a Gate B owner is required")
        self.__host = host

    @classmethod
    def overlap(
        cls,
        roots: Iterable[Path],
        *,
        root_ref: str,
        method: str = "synthesize",
    ) -> ManagedCallPort:
        return cls(LeasedReadinessHost(roots, root_ref=root_ref, method=method))

    @classmethod
    def exclusive(
        cls,
        roots: Iterable[Path],
        *,
        root_ref: str,
        is_resource_free: Callable[[], bool],
        method: str = "synthesize",
        model_asset_paths: Callable[[str], Iterable[Path]] | None = None,
        snapshot_config_prefix: str | None = None,
        snapshot_max_file_bytes: int = 16 * 1024 * 1024,
    ) -> ManagedCallPort:
        return cls(RealStopFirstHost(
            roots, root_ref=root_ref,
            is_resource_free=is_resource_free, method=method,
            model_asset_paths=model_asset_paths,
            snapshot_config_prefix=snapshot_config_prefix,
            snapshot_max_file_bytes=snapshot_max_file_bytes,
        ))

    def invoke(self, text: str) -> str | bytes:
        """One bounded synchronous invocation; no get() or plugin borrowing.

        The selected Gate B owner holds the lease until the method returns
        or raises. A str/bytes result cannot retain a live plugin instance.
        For audio streaming, futures or nested objects, a separate explicit
        lifetime contract will be required rather than leaking references.
        """
        if type(text) is not str:
            raise TypeError("managed input must be plain str")
        result = self.__host.call(text)
        if type(result) not in (str, bytes):
            raise DeferredOrMutableResult(
                "managed output must be detached str or bytes; "
                "deferred/mutable values are unsupported"
            )
        return result

    def upgrade(
        self, *, drain_timeout_s: float = 1.0,
        release_timeout_s: float = 1.0,
    ) -> int:
        """Host-owned, opt-in one-version upgrade. Returns catalog epoch.

        Overlap: prepare a READY successor while old calls run; publish with
        CAS and retire old generation after final lease.
        Exclusive: stage metadata, stop admission, drain, stop/reap, prove
        resource free, create new context, then publish. A FAILED resource
        fence is inherited from the existing StopFirstSlot.
        """
        if isinstance(self.__host, LeasedReadinessHost):
            stage = self.__host.stage()
            prepared = self.__host.prepare(stage)
            view = self.__host.publish_ready(prepared)
        else:
            staged = self.__host.stage()
            view = self.__host.replace(
                staged,
                drain_timeout_s=drain_timeout_s,
                release_timeout_s=release_timeout_s,
            )
        return view.epoch

    def close(
        self, *, drain_timeout_s: float = 1.0,
        release_timeout_s: float = 1.0,
    ) -> None:
        """Stop new admission; B.1 nonblocking vs B.2 bounded drain.

        Overlap close may return while existing leases are still finishing.
        Neither close path certifies GPU release except via the exclusive
        host's independent resource probe.
        """
        if isinstance(self.__host, LeasedReadinessHost):
            self.__host.close()
        else:
            self.__host.close(
                drain_timeout_s=drain_timeout_s,
                release_timeout_s=release_timeout_s,
            )

    def __enter__(self) -> ManagedCallPort:
        return self

    def __exit__(self, *_):
        self.close()
