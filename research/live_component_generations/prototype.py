"""Standalone standard-library experiment; NOT an Invariant Runtime API."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from typing import Callable, Iterator


class ProviderUnavailable(RuntimeError):
    """No active generation can be acquired."""


class GenerationConflict(RuntimeError):
    """Another switch committed while this candidate was starting."""


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    contract: str
    plugin_id: str
    revision: str
    start: Callable[[], object]
    stop: Callable[[object], None]

    @property
    def identity(self) -> tuple[str, str]:
        return self.plugin_id, self.revision


@dataclass(slots=True)
class _Generation:
    spec: ProviderSpec
    instance: object
    leases: int = 0
    retired: bool = False
    disposed: bool = False


class ProviderSlot:
    """A single host-selected contract binding, for research only.

    This is deliberately NOT a package installer, security boundary, process
    supervisor, GPU scheduler, or replacement for the current PluginService.
    """

    def __init__(self, contract: str) -> None:
        if not contract:
            raise ValueError("contract must not be empty")
        self.contract = contract
        self._lock = RLock()
        self._catalog: dict[tuple[str, str], ProviderSpec] = {}
        self._active: _Generation | None = None
        self._epoch = 0
        self._shutdown_errors: list[tuple[tuple[str, str], str]] = []

    def install(self, spec: ProviderSpec) -> None:
        """Register metadata only; do not start the provider."""
        if spec.contract != self.contract:
            raise ValueError("provider contract mismatch")
        if not spec.plugin_id or not spec.revision:
            raise ValueError("provider identity must be nonempty")
        with self._lock:
            if spec.identity in self._catalog:
                raise ValueError("provider revision is already installed")
            self._catalog[spec.identity] = spec

    def installed(self) -> tuple[tuple[str, str], ...]:
        with self._lock:
            return tuple(sorted(self._catalog))

    def active(self) -> tuple[str, str] | None:
        with self._lock:
            return self._active.spec.identity if self._active else None

    def shutdown_errors(self) -> tuple[tuple[tuple[str, str], str], ...]:
        with self._lock:
            return tuple(self._shutdown_errors)

    def switch(self, plugin_id: str, revision: str) -> bool:
        """Start candidate, then atomically change routing for new leases.

        Returns False if the requested generation is already active. A failed
        start leaves the current generation untouched. Two simultaneous
        switches cannot silently overwrite each other's routing decisions.
        Requires sufficient resources for an overlapping start: stop-first
        replacement is OUT OF SCOPE for this experiment.
        """
        key = (plugin_id, revision)
        with self._lock:
            spec = self._catalog[key]
            if self._active and self._active.spec.identity == key:
                return False
            expected_epoch = self._epoch

        instance = spec.start()  # potentially slow; never hold routing lock
        candidate = _Generation(spec, instance)
        retiring: _Generation | None = None
        conflict = False
        with self._lock:
            if self._epoch != expected_epoch:
                conflict = True
                candidate.disposed = True
            else:
                previous = self._active
                self._active = candidate
                self._epoch += 1
                if previous is not None:
                    previous.retired = True
                    if previous.leases == 0 and not previous.disposed:
                        previous.disposed = True
                        retiring = previous

        if conflict:
            self._dispose(candidate)
            raise GenerationConflict("binding changed while candidate was starting")
        if retiring is not None:
            self._dispose(retiring)
        return True

    @contextmanager
    def acquire(self) -> Iterator[object]:
        """Pin the current generation for the duration of a managed call."""
        with self._lock:
            generation = self._active
            if generation is None:
                raise ProviderUnavailable(self.contract)
            generation.leases += 1
        try:
            yield generation.instance
        finally:
            retiring: _Generation | None = None
            with self._lock:
                generation.leases -= 1
                if generation.retired and generation.leases == 0 and not generation.disposed:
                    generation.disposed = True
                    retiring = generation
            if retiring is not None:
                self._dispose(retiring)

    def deactivate(self) -> None:
        """Reject new leases and drain current leases before disposal."""
        retiring: _Generation | None = None
        with self._lock:
            previous = self._active
            if previous is None:
                return
            self._active = None
            self._epoch += 1
            previous.retired = True
            if previous.leases == 0 and not previous.disposed:
                previous.disposed = True
                retiring = previous
        if retiring is not None:
            self._dispose(retiring)

    def _dispose(self, generation: _Generation) -> None:
        try:
            generation.spec.stop(generation.instance)
        except Exception as exc:
            with self._lock:
                self._shutdown_errors.append((generation.spec.identity, repr(exc)))
