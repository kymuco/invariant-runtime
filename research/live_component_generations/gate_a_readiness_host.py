"""Gate A.5: single-pointer routing of *already initialized* plugin generations.

Research only. No production PluginService API, no policy changes, no
generation leases, no GPU/resource reclamation, no concurrency-safe close.
Only one named root with mandatory EXECUTION dependencies is admitted.

A published route owns both its retained CatalogView and the already READY
real ExecutionContext/instance. No separately authoritative registry or
routing map is consulted for calls. Old contexts are retained until the
owner is explicitly closed by a QUIESCED caller (Gate B will need leases).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Iterable

from catalog_epoch import CatalogConflict, CatalogEpochs, StalePlan
from gate_a_catalog_owned_host import discover_candidates, research_manifest_loader
from gate_a_catalog_view import CatalogView
from gate_a_pinned_execution import open_pinned_execution


@dataclass(frozen=True, slots=True)
class PublishedRoute:
    view: CatalogView
    context: object
    plugin: object
    root_key: str


@dataclass(frozen=True, slots=True)
class StagedRoute:
    base: PublishedRoute
    candidate: CatalogView
    signature: tuple[tuple[str, str, tuple[str, ...]], ...]
    added: tuple[str, ...]
    root_key: str


@dataclass(slots=True)
class PreparedRoute:
    owner: ReadinessGatedHost
    stage: StagedRoute
    context: object
    plugin: object
    status: str = "prepared"


class ReadinessGatedHost:
    """A single active pointer to (view + ready instance); research only."""

    def __init__(
        self,
        roots: Iterable[Path],
        *,
        root_ref: str,
        method: str = "synthesize",
    ):
        self.roots = tuple(Path(root).resolve() for root in roots)
        if not self.roots:
            raise ValueError("roots required")
        if not root_ref or "@" in root_ref:
            raise ValueError("root_ref must be an unversioned named plugin id")
        if not isinstance(method, str) or not method.isidentifier():
            raise ValueError("method must be a valid method name")

        self.loader = research_manifest_loader(self.roots)
        self.tracer = None
        self.root_ref = root_ref
        self.method = method
        self._lock = RLock()
        self._closed = False
        self._retired: list[PublishedRoute] = []
        self._pending: list[PreparedRoute] = []

        initial = CatalogView(1, self._discover())
        root = initial.resolve_graph(root_ref).root
        context = open_pinned_execution(self, initial)
        try:
            instance = context.get(root)
            self._ensure_method(instance)
        except BaseException:
            context.close()
            raise
        self._active = PublishedRoute(initial, context, instance, root)

    def _discover(self):
        return discover_candidates(self.roots, self.loader)

    @staticmethod
    def _signature(candidates):
        return CatalogEpochs._signature(CatalogEpochs._normalize(candidates))

    def _ensure_method(self, plugin) -> None:
        if not callable(getattr(plugin, self.method, None)):
            raise CatalogConflict(f"missing required method {self.method}")

    def snapshot(self) -> CatalogView:
        with self._lock:
            self._require_open()
            return self._active.view

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("readiness-gated research host is closed")

    def call(self, *args, **kwargs):
        """One synchronous method call on a retained published generation.

        Do not invoke close concurrently with call: no leases exist in Gate A.
        """
        with self._lock:
            self._require_open()
            selected = self._active
        return getattr(selected.plugin, self.method)(*args, **kwargs)

    def stage(self) -> StagedRoute:
        with self._lock:
            self._require_open()
            base = self._active
        entries = CatalogEpochs._normalize(self._discover())
        old_entries = base.view.entries
        for key, old in old_entries.items():
            found = entries.get(key)
            if found is None:
                raise CatalogConflict(f"silent removal forbidden: {key}")
            if (old.fingerprint, old.dependencies) != (found.fingerprint, found.dependencies):
                raise CatalogConflict(f"same-version artifact mutation forbidden: {key}")
        added = tuple(sorted(entries.keys() - old_entries.keys()))
        if not added:
            raise CatalogConflict("readiness gate requires a new root version")
        candidate = CatalogView(base.view.epoch + 1, entries.values())
        root_key = candidate.resolve_graph(self.root_ref).root
        if root_key not in added:
            raise CatalogConflict("selected root version is not new")
        return StagedRoute(
            base, candidate, CatalogEpochs._signature(entries), added, root_key
        )

    def prepare(self, stage: StagedRoute) -> PreparedRoute:
        """Instantiate the real candidate, with no active-route publication."""
        with self._lock:
            self._require_open()
            if self._active is not stage.base:
                raise StalePlan("active route changed before preparation")
        context = open_pinned_execution(self, stage.candidate)
        try:
            plugin = context.get(stage.root_key)
            self._ensure_method(plugin)
            # Note: a successful initialize proves READY now, not forever.
            with self._lock:
                self._require_open()
                if self._active is not stage.base:
                    raise StalePlan("active route changed while preparing")
                ready = PreparedRoute(self, stage, context, plugin)
                self._pending.append(ready)
                return ready
        except BaseException:
            context.close()
            raise

    def discard(self, prepared: PreparedRoute) -> None:
        """Explicit abort closes the uncommitted candidate."""
        if prepared.owner is not self:
            raise CatalogConflict("prepared route belongs to a different owner")
        with self._lock:
            if prepared.status != "prepared":
                return
            prepared.status = "discarded"
            if prepared in self._pending:
                self._pending.remove(prepared)
        prepared.context.close()

    def publish_ready(self, prepared: PreparedRoute) -> CatalogView:
        """CAS publish ONE pointer to catalog+READY instance, or fail closed."""
        if prepared.owner is not self:
            raise CatalogConflict("prepared route belongs to a different owner")
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
                # Keep retired generations alive: no unsafe shutdown while
                # callers may still be holding or using their plugin objects.
                self._retired.append(self._active)
                self._pending.remove(prepared)
                prepared.status = "published"
                # Exactly one published state changes, after readiness check.
                self._active = PublishedRoute(
                    stage.candidate, prepared.context, prepared.plugin, stage.root_key
                )
                return self._active.view
        except BaseException:
            self.discard(prepared)
            raise

    def close(self) -> None:
        """Call only after external quiescence; no Gate B leases yet."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            to_close = (
                [item.context for item in self._pending]
                + [self._active.context]
                + [route.context for route in self._retired]
            )
            for item in self._pending:
                item.status = "discarded"
            self._pending.clear()
            self._retired.clear()
        for context in reversed(to_close):
            context.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
