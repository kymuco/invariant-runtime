"""Research-only atomic *metadata* epochs, independent of Invariant internals.

The caller owns the discovery/validation and registry-publication boundary.
Monotonic add-only epochs do not permit same-version mutation, silent removal,
or stale staged-plan commits. Pure stdlib, Python >=3.11.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from typing import Callable, Iterable, Mapping


class CatalogConflict(RuntimeError):
    """A prospective catalog violates an existing immutable generation."""


class StalePlan(RuntimeError):
    """Discovery or the active epoch changed after staging."""


@dataclass(frozen=True, slots=True)
class Candidate:
    key: str
    fingerprint: str
    dependencies: tuple[str, ...] = ()
    payload: object = None


@dataclass(frozen=True, slots=True)
class CatalogPlan:
    base_epoch: int
    entries: tuple[Candidate, ...]
    added: tuple[str, ...]
    signature: tuple[tuple[str, str, tuple[str, ...]], ...]


class CatalogEpochs:
    """Atomic catalog pointer under one lock; no plugin instances are touched."""

    def __init__(self, initial: Iterable[Candidate] = ()) -> None:
        self._lock = RLock()
        self._current = MappingProxyType({})
        self._epoch = 0
        first = tuple(initial)
        if first:
            plan = self.stage(first)
            self.publish(plan, first)

    @property
    def epoch(self) -> int:
        with self._lock:
            return self._epoch

    def snapshot(self) -> tuple[int, Mapping[str, Candidate]]:
        with self._lock:
            return self._epoch, self._current

    @staticmethod
    def _normalize(entries: Iterable[Candidate]) -> dict[str, Candidate]:
        found: dict[str, Candidate] = {}
        for c in entries:
            if not c.key or not c.fingerprint or '@' not in c.key or c.key.endswith('@latest'):
                raise CatalogConflict('exact id@version and nonempty fingerprint required')
            if c.key in found:
                raise CatalogConflict(f'duplicate version: {c.key}')
            for dep in c.dependencies:
                if '@' not in dep or dep.endswith('@latest'):
                    raise CatalogConflict(f'unpinned dependency: {c.key} -> {dep}')
            found[c.key] = c
        visiting: set[str] = set()
        visited: set[str] = set()
        def visit(key: str) -> None:
            if key in visiting:
                raise CatalogConflict(f'dependency cycle at {key}')
            if key in visited:
                return
            if key not in found:
                raise CatalogConflict(f'missing pinned dependency: {key}')
            visiting.add(key)
            for dep in found[key].dependencies:
                visit(dep)
            visiting.remove(key)
            visited.add(key)
        for key in sorted(found):
            visit(key)
        return found

    @staticmethod
    def _signature(found: Mapping[str, Candidate]) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
        return tuple(sorted((c.key, c.fingerprint, c.dependencies) for c in found.values()))

    def stage(self, candidates: Iterable[Candidate]) -> CatalogPlan:
        found = self._normalize(candidates)
        with self._lock:
            for key, previous in self._current.items():
                candidate = found.get(key)
                if candidate is None:
                    raise CatalogConflict(f'silent removal forbidden: {key}')
                if (candidate.fingerprint, candidate.dependencies) != (previous.fingerprint, previous.dependencies):
                    raise CatalogConflict(f'in-place version mutation forbidden: {key}')
            return CatalogPlan(self._epoch, tuple(found[key] for key in sorted(found)),
                               tuple(sorted(found.keys() - self._current.keys())), self._signature(found))

    def publish(
        self,
        plan: CatalogPlan,
        freshly_discovered: Iterable[Candidate],
        *,
        commit: Callable[[Mapping[str, Candidate], tuple[str, ...]], None] | None = None,
    ) -> int:
        """Require fresh discovery; commit callback must be all-or-nothing."""
        found = self._normalize(freshly_discovered)
        with self._lock:
            if plan.base_epoch != self._epoch:
                raise StalePlan('epoch changed after staging')
            if self._signature(found) != plan.signature:
                raise StalePlan('discovery changed after staging')
            if not plan.added:
                return self._epoch
            expected = dict(self._current)
            if not expected.keys() <= found.keys():
                raise StalePlan('previously published build was removed')
            for key, old in expected.items():
                if (old.fingerprint, old.dependencies) != (found[key].fingerprint, found[key].dependencies):
                    raise StalePlan('previously published build changed')
            if commit is not None:
                commit(MappingProxyType(found), plan.added)
            self._current = MappingProxyType(dict(found))
            self._epoch += 1
            return self._epoch
