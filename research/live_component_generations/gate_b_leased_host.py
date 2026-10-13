"""Gate B.1: leased, readiness-gated real-plugin routing, research only.

This class builds on Gate A.5's real Invariant ExecutionContext/loader.
One lock protects admissions, the active route pointer and retirement.
Every accepted call owns a lease until it returns (or raises); only then
may the owning ExecutionContext be shut down. Shutdown runs OUTSIDE lock.

No unbounded raw managed get(), no cross-policy/SINGLETON guarantees, no
resource-free proof, no exclusive-GPU stop-first, no asynchronous jobs.
Public APIs in invariant/ are not modified.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from catalog_epoch import CatalogConflict, StalePlan
from gate_a_readiness_host import (
    PreparedRoute,
    PublishedRoute,
    ReadinessGatedHost,
    StagedRoute,
)
from gate_a_catalog_view import CatalogView


@dataclass(eq=False, slots=True)
class _OwnedGeneration:
    route: PublishedRoute
    users: int = 0
    retired: bool = False
    disposing: bool = False


class LeasedReadinessHost(ReadinessGatedHost):
    """Opt-in Gate B wrapper: retain old generations only while leased.

    The inherited _active is the ONE authoritative published route. Generation
    records below track ownership/lifetime, not a competing route/catalog.
    """

    def __init__(self, roots, *, root_ref: str, method: str = "synthesize"):
        super().__init__(roots, root_ref=root_ref, method=method)
        initial = _OwnedGeneration(self._active)
        self._owned: dict[int, _OwnedGeneration] = {id(self._active): initial}
        self._shutdown_errors: list[tuple[str, str]] = []
        self._failed_pending: list[PreparedRoute] = []

    def _retire_locked(self, owner: _OwnedGeneration) -> bool:
        """Claim disposal exactly once. Caller MUST hold self._lock."""
        owner.retired = True
        if owner.users == 0 and not owner.disposing:
            owner.disposing = True
            return True
        return False

    def _dispose(self, owner: _OwnedGeneration) -> None:
        """Close once, outside lock; quarantine failed releases without retry."""
        try:
            owner.route.context.close()
        except BaseException as exc:
            with self._lock:
                self._shutdown_errors.append((owner.route.root_key, repr(exc)))
                # Preserve ownership evidence: this generation is still
                # tracked as retired/disposing, NOT certified released.
                # Never retry shutdown or route calls through it silently.
        else:
            with self._lock:
                self._owned.pop(id(owner.route), None)

    @contextmanager
    def acquire(self) -> Iterator[object]:
        """Lease the active plugin for a bounded, synchronous operation.

        The borrowed object MUST NOT escape the context. The context manager
        is the sole approved research usage path for the active generation.
        """
        with self._lock:
            self._require_open()
            # Resolve ownership via the ONE published _active route.
            owner = self._owned[id(self._active)]
            owner.users += 1
        try:
            yield owner.route.plugin
        finally:
            dispose = False
            with self._lock:
                owner.users -= 1
                if owner.users < 0:
                    raise RuntimeError("lease accounting underflow")
                if owner.retired and owner.users == 0 and not owner.disposing:
                    owner.disposing = True
                    dispose = True
            if dispose:
                self._dispose(owner)

    def call(self, *args, **kwargs):
        """Pin one generation for the whole call; propagate plugin errors."""
        with self.acquire() as plugin:
            return getattr(plugin, self.method)(*args, **kwargs)

    def publish_ready(self, prepared: PreparedRoute) -> CatalogView:
        """CAS publish only a prepared READY candidate; drain old leases.

        Candidate discovery is outside lock. Old generation cleanup is after
        the single active-pointer update and always outside the lock.
        """
        if prepared.owner is not self:
            raise CatalogConflict("prepared route belongs to a different owner")
        dispose_old = None
        try:
            fresh = self._signature(self._discover())
            with self._lock:
                self._require_open()
                if prepared.status != "prepared" or prepared not in self._pending:
                    raise CatalogConflict("candidate was already consumed or discarded")
                stage = prepared.stage
                if self._active is not stage.base:
                    raise StalePlan("active route changed before publication")
                if fresh != stage.signature:
                    raise StalePlan("source artifacts changed after staging")
                if not callable(getattr(prepared.plugin, self.method, None)):
                    raise CatalogConflict("prepared instance lost its required method")
                prior = self._owned[id(self._active)]
                successor = PublishedRoute(
                    stage.candidate, prepared.context, prepared.plugin, stage.root_key
                )
                new_owner = _OwnedGeneration(successor)
                self._owned[id(successor)] = new_owner
                self._pending.remove(prepared)
                prepared.status = "published"
                self._active = successor  # the ONLY published route pointer
                # _active is the sole routing pointer; ownership is
                # indexed only by the retained route identity.
                if self._retire_locked(prior):
                    dispose_old = prior
                view = successor.view
        except BaseException:
            self.discard(prepared)
            raise
        if dispose_old is not None:
            self._dispose(dispose_old)
        return view

    def close(self) -> None:
        """Nonblocking admission stop; idle owners close, leased owners drain.

        A lease holder releases later and triggers teardown. close() returning
        does NOT mean that all physical resources have been freed.
        No resource may be stopped while this host still tracks an in-flight
        call. Prepared-but-unpublished instances are immediately discarded.
        """
        to_close = []
        pending = []
        with self._lock:
            if self._closed:
                return
            self._closed = True
            pending = list(self._pending)
            for candidate in pending:
                candidate.status = "discarded"
            self._pending.clear()
            for owner in tuple(self._owned.values()):
                if self._retire_locked(owner):
                    to_close.append(owner)
        for candidate in pending:
            try:
                candidate.context.close()
            except BaseException as exc:
                with self._lock:
                    self._shutdown_errors.append((candidate.stage.root_key, repr(exc)))
                    # Retain failed unpublished candidate for diagnostics.
                    self._failed_pending.append(candidate)
        for owner in to_close:
            self._dispose(owner)

    def live_generations(self) -> tuple[tuple[str, int, bool], ...]:
        """Snapshot (exact root, active leases, retired) for research tests."""
        with self._lock:
            return tuple(sorted(
                (item.route.root_key, item.users, item.retired)
                for item in self._owned.values()
            ))

    def shutdown_errors(self) -> tuple[tuple[str, str], ...]:
        with self._lock:
            return tuple(self._shutdown_errors)
