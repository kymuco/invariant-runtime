"""Research-only, single-pointer catalog snapshots and pinned dependency graphs.

This is NOT an in-place PluginService refresh implementation or public API.
A CatalogView is constructed from real Invariant descriptors before publication;
resolution uses a private registry created once for that view and never exposed.
Publication only replaces the coordinator's current-view pointer.  It does
not alter PluginService.registry, activate providers, or tear down instances.

The caller must provide freshly verified, immutable artifact identities at
stage AND publish. Fingerprints supplied by the caller are not cryptographic
attestations merely because they are strings. This is metadata consistency.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from typing import Iterable, Mapping

from catalog_epoch import Candidate, CatalogConflict, CatalogEpochs, StalePlan


@dataclass(frozen=True, slots=True)
class ResolvedGraph:
    """Exact graph resolved against one retained view (no dynamic re-binding)."""

    view: CatalogView
    root: str
    order: tuple[str, ...]

    @property
    def epoch(self) -> int:
        return self.view.epoch


class CatalogView:
    """Read-only facade over a dedicated real Invariant registry per epoch.

    Python cannot prevent deliberate private-attribute manipulation or a
    consumer mutating descriptor objects. This is a scoped research contract.
    """

    def __init__(self, epoch: int, candidates: Iterable[Candidate]):
        from invariant.catalog.registry import PluginRegistry
        from invariant.exec.runtime.resolver import DependencyResolver

        entries = CatalogEpochs._normalize(candidates)
        registry = PluginRegistry(mode="strict")
        for key in sorted(entries):
            descriptor = entries[key].payload
            meta = getattr(descriptor, "meta", None)
            if meta is None or f"{meta.id}@{meta.version}" != key:
                raise CatalogConflict(f"descriptor identity does not match {key}")
            registry.register(descriptor)
        self.epoch = epoch
        self._entries: Mapping[str, Candidate] = MappingProxyType(dict(entries))
        self.__registry = registry
        # Real resolver preflight: a failed graph cannot become a published view.
        for key in sorted(entries):
            DependencyResolver(registry=self).collect(key)

    @property
    def entries(self) -> Mapping[str, Candidate]:
        return self._entries

    def resolve(self, ref: str):
        """Match real Invariant id/latest resolution, fixed to this view."""
        return self.__registry.resolve(ref)

    def resolve_graph(self, ref: str) -> ResolvedGraph:
        from invariant.exec.runtime.resolver import DependencyResolver

        resolver = DependencyResolver(registry=self)
        resolver.collect(ref)
        order = tuple(resolver.execution_order())
        # The canonical root may be selected using a latest/unpinned reference.
        descriptor = self.resolve(ref)
        root = f"{descriptor.meta.id}@{descriptor.meta.version}"
        return ResolvedGraph(self, root, order)


@dataclass(frozen=True, slots=True)
class Stage:
    base: CatalogView
    candidate: CatalogView
    signature: tuple[tuple[str, str, tuple[str, ...]], ...]
    added: tuple[str, ...]


class CatalogViews:
    """CAS publication over one authoritative view pointer, no commit callback."""

    def __init__(self, initial: Iterable[Candidate]):
        self._lock = RLock()
        self._current = CatalogView(1, tuple(initial))

    def snapshot(self) -> CatalogView:
        with self._lock:
            return self._current

    @staticmethod
    def _signature(candidates: Iterable[Candidate]):
        entries = CatalogEpochs._normalize(candidates)
        return CatalogEpochs._signature(entries)

    def stage(self, candidates: Iterable[Candidate]) -> Stage:
        entries = tuple(candidates)
        with self._lock:
            before = self._current
        new_map = CatalogEpochs._normalize(entries)
        old = before.entries
        for key, previous in old.items():
            entry = new_map.get(key)
            if entry is None:
                raise CatalogConflict(f"silent removal forbidden: {key}")
            if (entry.fingerprint, entry.dependencies) != (previous.fingerprint, previous.dependencies):
                raise CatalogConflict(f"in-place version mutation forbidden: {key}")
        added = tuple(sorted(new_map.keys() - old.keys()))
        # Validation/build is done before acquiring the publication lock.
        candidate = CatalogView(before.epoch + 1, entries) if added else before
        return Stage(before, candidate, self._signature(entries), added)

    def publish(self, stage: Stage, freshly_discovered: Iterable[Candidate]) -> CatalogView:
        # Fresh validation is a caller-owned disk/artifact responsibility.
        signature = self._signature(freshly_discovered)
        with self._lock:
            if self._current is not stage.base:
                raise StalePlan("catalog epoch changed after staging")
            if signature != stage.signature:
                raise StalePlan("catalog artifacts changed after staging")
            # One pointer assignment is the only visibility point.
            if stage.added:
                self._current = stage.candidate
            return self._current
