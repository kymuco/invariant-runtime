"""Gate B.2: actual Invariant plugin stop-first routing, research only.

A host-provided resource-free predicate is the only authority to permit
exclusive candidate startup. The prior instance must fully drain/stop
before the new actual ExecutionContext is constructed. The existing
StopFirstSlot is the SINGLE route/catalog visibility owner.

This is not a GPU release monitor: tests use a cross-instance filesystem
exclusion token. In production, real host-owned resource evidence is needed.
Not a public PluginService API, not a multi-scope runtime, not code reload.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Callable, Iterable
from uuid import uuid4

from catalog_epoch import CatalogConflict, CatalogEpochs, StalePlan
from gate_a_catalog_owned_host import discover_candidates, research_manifest_loader
from gate_a_catalog_view import CatalogView
from gate_a_pinned_execution import open_pinned_execution
from gate_a_readiness_host import PublishedRoute
from prototype import ProviderSpec, ProviderUnavailable
from stop_first import Phase, Snapshot, StopFirstSlot


@dataclass(frozen=True, slots=True)
class ExclusiveStage:
    owner: object
    base_identity: tuple[str, str]
    view: CatalogView
    root_key: str
    signature: tuple[tuple[str, str, tuple[str, ...]], ...]
    ticket: str


class RealStopFirstHost:
    """One named root, mandatory EXECUTION dependencies, synchronous method.

    The exclusive slot holds the only effective route. Stage provides
    validated *metadata* but does not prepare (and cannot allocate) the
    candidate while the old plugin still owns the exclusive resource.
    """

    def __init__(
        self,
        roots: Iterable[Path],
        *,
        root_ref: str,
        is_resource_free: Callable[[], bool],
        method: str = "synthesize",
    ):
        if not root_ref or "@" in root_ref:
            raise ValueError("root_ref must be an unversioned plugin identifier")
        if not callable(is_resource_free):
            raise TypeError("host-owned resource probe is required")
        if not isinstance(method, str) or not method.isidentifier():
            raise ValueError("invalid method name")
        self.roots = tuple(Path(p).resolve() for p in roots)
        if not self.roots:
            raise ValueError("roots required")
        self.loader = research_manifest_loader(self.roots)
        self.tracer = None
        self.root_ref = root_ref
        self.method = method
        self._management = RLock()
        self._installed: set[str] = set()
        self._slot = StopFirstSlot(root_ref, is_resource_free=is_resource_free)
        initial = CatalogView(1, discover_candidates(self.roots, self.loader))
        root = initial.resolve_graph(root_ref).root
        identity = self._install(initial, root, "initial")
        self._slot.replace(*identity)

    @staticmethod
    def _signature(candidates):
        found = CatalogEpochs._normalize(candidates)
        return CatalogEpochs._signature(found)

    def _install(self, view: CatalogView, root_key: str, ticket: str):
        # Each ticket is a distinct research activation attempt even if a
        # drain timeout later permits retry of that same stage.
        spec = ProviderSpec(
            self.root_ref,
            self.root_ref,
            ticket,
            lambda: self._start(view, root_key),
            self._stop,
        )
        self._slot.install(spec)
        self._installed.add(ticket)
        return spec.identity

    def _start(self, view: CatalogView, root_key: str):
        # Called ONLY after stop-first has verified is_resource_free().
        context = open_pinned_execution(self, view)
        try:
            plugin = context.get(root_key)
            if not callable(getattr(plugin, self.method, None)):
                raise CatalogConflict("required method missing after initialization")
        except BaseException:
            context.close()
            raise
        return PublishedRoute(view, context, plugin, root_key)

    @staticmethod
    def _stop(route: PublishedRoute):
        # Invariant Runtime may catch shutdown exceptions internally, so the
        # host NEVER equates a successful close return with release evidence.
        route.context.close()

    def status(self) -> Snapshot:
        return self._slot.snapshot()

    def snapshot(self) -> CatalogView:
        with self._slot.acquire() as route:
            return route.view

    def call(self, *args, **kwargs):
        with self._slot.acquire() as route:
            return getattr(route.plugin, self.method)(*args, **kwargs)

    def stage(self) -> ExclusiveStage:
        # Snapshot is protected by one admission lease. During an exclusive
        # stop-first transition, admission fails (no falsely stable routing).
        current = self._slot.snapshot()
        if current.phase is not Phase.ACTIVE or current.active is None:
            raise ProviderUnavailable("exclusive provider is unavailable")
        with self._slot.acquire() as old:
            base = old.view
        found = CatalogEpochs._normalize(discover_candidates(self.roots, self.loader))
        for key, prior in base.entries.items():
            new = found.get(key)
            if new is None:
                raise CatalogConflict(f"silent removal forbidden: {key}")
            if (new.fingerprint, new.dependencies) != (prior.fingerprint, prior.dependencies):
                raise CatalogConflict(f"same-version mutation forbidden: {key}")
        added = set(found) - set(base.entries)
        candidate = CatalogView(base.epoch + 1, found.values())
        root_key = candidate.resolve_graph(self.root_ref).root
        if root_key not in added:
            raise CatalogConflict("exclusive switch requires new selected root version")
        return ExclusiveStage(
            owner=self,
            base_identity=current.active,
            view=candidate,
            root_key=root_key,
            signature=CatalogEpochs._signature(found),
            ticket=uuid4().hex,
        )

    def replace(
        self,
        staged: ExclusiveStage,
        *,
        drain_timeout_s: float = 1.0,
        release_timeout_s: float = 1.0,
    ):
        """Metadata preflight -> drain -> stop -> prove free -> start -> route.

        Returns the newly published CatalogView, or raises with the slot
        phase reflecting safe recovery/failure. Candidate start never occurs
        when release evidence is absent. FAILED requires external recovery.
        """
        if staged.owner is not self:
            raise CatalogConflict("stage belongs to a different host")
        if drain_timeout_s < 0 or release_timeout_s < 0:
            raise ValueError("timeouts must be nonnegative")
        with self._management:
            # Validate before fencing admission or tearing down old plugin.
            if self._signature(discover_candidates(self.roots, self.loader)) != staged.signature:
                raise StalePlan("source changed after exclusive stage")
            status = self._slot.snapshot()
            if self._slot.is_closed:
                raise RuntimeError("exclusive slot is closed")
            if status.phase is Phase.FAILED:
                raise RuntimeError("exclusive slot FAILED; manual recovery required")
            if status.phase is not Phase.ACTIVE or status.active != staged.base_identity:
                raise StalePlan("active exclusive route changed since staging")
            if staged.ticket not in self._installed:
                self._install(staged.view, staged.root_key, staged.ticket)
            self._slot.replace(
                self.root_ref,
                staged.ticket,
                drain_timeout_s=drain_timeout_s,
                release_timeout_s=release_timeout_s,
            )
            return self.snapshot()

    def close(self, *, drain_timeout_s=1.0, release_timeout_s=1.0):
        with self._management:
            self._slot.close(
                drain_timeout_s=drain_timeout_s,
                release_timeout_s=release_timeout_s,
            )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
