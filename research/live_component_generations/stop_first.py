"""Stop-first exclusive-resource replacement research; NOT a public API.

Explicit continuity boundary: the host stays up, but the provider is unavailable
between beginning drain and successful replacement/restore.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from threading import Condition, Lock, RLock
from typing import Callable, Iterator

from prototype import ProviderSpec, ProviderUnavailable


class Phase(str, Enum):
    EMPTY = "empty"
    ACTIVE = "active"
    DRAINING = "draining"
    STOPPING = "stopping"
    AWAITING_RELEASE = "awaiting_release"
    STARTING = "starting"
    RESTORING = "restoring"
    FAILED = "failed"


class DrainTimeout(RuntimeError):
    """The old generation is still borrowed; no stop was attempted."""


class UnsafeRelease(RuntimeError):
    """Resource release was not proven; do not start any new generation."""


class StopFailed(RuntimeError):
    """Stopping the old instance failed; its ownership is uncertain."""


class CandidateFailed(RuntimeError):
    """Candidate startup failed; restored says whether a new old instance is ready."""

    def __init__(self, *, restored: bool) -> None:
        super().__init__(f"candidate startup failed; restored={restored}")
        self.restored = restored


@dataclass(frozen=True, slots=True)
class Snapshot:
    phase: Phase
    active: tuple[str, str] | None
    users: int


class StopFirstSlot:
    """Exclusive resource experiment with bounded drain and failure fencing.

    install() is metadata-only. acquire() provides the sole tracked use path.
    replace() serializes upgrades, rejects new uses during a replacement,
    drains old leases, stops old before starting candidate, and only tries
    to recover old if resource-release evidence permits it.

    start()/stop() are supplied by the host's test doubles in this experiment.
    They are assumed synchronous, bounded, and not to call this slot recursively.
    """

    def __init__(self, contract: str, *, is_resource_free: Callable[[], bool]) -> None:
        if not contract:
            raise ValueError("contract must not be empty")
        self.contract = contract
        self._is_resource_free = is_resource_free
        self._cv = Condition(RLock())
        self._update = Lock()
        self._catalog: dict[tuple[str, str], ProviderSpec] = {}
        self._phase = Phase.EMPTY
        self._active: tuple[ProviderSpec, object] | None = None
        self._users = 0
        self._events: list[str] = []
        self._closed = False

    def install(self, spec: ProviderSpec) -> None:
        if spec.contract != self.contract or not all(spec.identity):
            raise ValueError("invalid provider contract or identity")
        with self._cv:
            if self._closed:
                raise RuntimeError("exclusive slot is closed")
            if spec.identity in self._catalog:
                raise ValueError("revision already installed")
            self._catalog[spec.identity] = spec

    def snapshot(self) -> Snapshot:
        with self._cv:
            identity = self._active[0].identity if self._phase is Phase.ACTIVE and self._active else None
            return Snapshot(self._phase, identity, self._users)

    def events(self) -> tuple[str, ...]:
        with self._cv:
            return tuple(self._events)

    def _set(self, phase: Phase) -> None:
        with self._cv:
            self._phase = phase
            self._events.append(phase.value)
            self._cv.notify_all()

    @contextmanager
    def acquire(self) -> Iterator[object]:
        with self._cv:
            if self._phase is not Phase.ACTIVE or self._active is None:
                raise ProviderUnavailable("provider is not accepting new operations")
            instance = self._active[1]
            self._users += 1
        try:
            yield instance
        finally:
            with self._cv:
                self._users -= 1
                self._cv.notify_all()

    def _await_free(self, deadline: float) -> None:
        self._set(Phase.AWAITING_RELEASE)
        while True:
            if self._is_resource_free():
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise UnsafeRelease("exclusive resource was not released before deadline")
            # A research-only bounded poll. Production would need a resource
            # monitor and worker death/liveness evidence rather than only this.
            time.sleep(min(remaining, 0.005))

    def replace(
        self,
        plugin_id: str,
        revision: str,
        *,
        drain_timeout_s: float = 1.0,
        release_timeout_s: float = 1.0,
    ) -> bool:
        """Switch with an explicit unavailable interval and best-effort restore.

        Returns False if already active. Raises with explicit FAILED state if
        release cannot be proved or old stop fails. On candidate startup error,
        restarts the old implementation *only if the resource is free*; this
        is not state migration or external-effect rollback.
        """
        if drain_timeout_s < 0 or release_timeout_s < 0:
            raise ValueError("timeouts must be nonnegative")
        key = (plugin_id, revision)
        with self._update:
            with self._cv:
                if self._closed:
                    raise RuntimeError("exclusive slot is closed")
                spec = self._catalog[key]  # fail before changing admission
                if self._phase is Phase.FAILED:
                    raise RuntimeError("slot is FAILED; manual recovery required")
                if self._active and self._phase is Phase.ACTIVE and self._active[0].identity == key:
                    return False
                if self._phase not in (Phase.ACTIVE, Phase.EMPTY):
                    raise RuntimeError("unexpected slot phase")
                previous = self._active
                self._set(Phase.DRAINING)
                deadline = time.monotonic() + drain_timeout_s
                while self._users:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._set(Phase.ACTIVE)
                        raise DrainTimeout("outstanding leases exceeded drain deadline")
                    self._cv.wait(timeout=remaining)
                self._set(Phase.STOPPING)

            if previous is not None:
                try:
                    previous[0].stop(previous[1])
                except Exception as exc:
                    # Keep the object available for diagnostics/manual recovery;
                    # no new borrow is permitted while the slot is FAILED.
                    self._set(Phase.FAILED)
                    raise StopFailed("old provider stop failed; resource ownership uncertain") from exc
                with self._cv:
                    self._active = None

            try:
                self._await_free(time.monotonic() + release_timeout_s)
            except Exception:
                self._set(Phase.FAILED)
                raise

            self._set(Phase.STARTING)
            try:
                candidate = spec.start()
            except Exception as exc:
                # A failed start may still have allocated resources; do not
                # attempt restart if that possibility remains.
                try:
                    self._await_free(time.monotonic() + release_timeout_s)
                except Exception as release_exc:
                    self._set(Phase.FAILED)
                    raise UnsafeRelease("candidate failed and resources remain held") from release_exc
                if previous is None:
                    self._set(Phase.EMPTY)
                    raise CandidateFailed(restored=False) from exc
                self._set(Phase.RESTORING)
                try:
                    restored = previous[0].start()
                except Exception as restore_exc:
                    self._set(Phase.FAILED)
                    raise CandidateFailed(restored=False) from restore_exc
                with self._cv:
                    self._active = (previous[0], restored)
                    self._set(Phase.ACTIVE)
                raise CandidateFailed(restored=True) from exc

            with self._cv:
                self._active = (spec, candidate)
                self._set(Phase.ACTIVE)
            return True

    def close(
        self,
        *,
        drain_timeout_s: float = 1.0,
        release_timeout_s: float = 1.0,
    ) -> None:
        """Terminal stop-first drain for host-owned research fixtures.

        Refuse new admission immediately during draining. A drain timeout
        restores ACTIVE admission so the caller may retry rather than
        silently interrupting an in-flight call. A stop/release failure
        leaves FAILED and fences all new calls. No release is presumed
        merely from successful invocation of provider.stop().
        """
        if drain_timeout_s < 0 or release_timeout_s < 0:
            raise ValueError("timeouts must be nonnegative")
        with self._update:
            with self._cv:
                if self._closed:
                    return
                if self._phase is Phase.FAILED:
                    self._closed = True
                    raise UnsafeRelease("slot already FAILED; manual resource recovery required")
                if self._phase not in (Phase.EMPTY, Phase.ACTIVE):
                    raise RuntimeError("unexpected phase during close")
                previous = self._active
                if previous is None:
                    self._closed = True
                    self._set(Phase.EMPTY)
                    return
                self._set(Phase.DRAINING)
                deadline = time.monotonic() + drain_timeout_s
                while self._users:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._set(Phase.ACTIVE)
                        raise DrainTimeout("outstanding leases exceeded close deadline")
                    self._cv.wait(timeout=remaining)
                self._closed = True
                self._set(Phase.STOPPING)
            try:
                previous[0].stop(previous[1])
            except Exception as exc:
                self._set(Phase.FAILED)
                raise StopFailed("final provider stop failed; resource ownership uncertain") from exc
            with self._cv:
                self._active = None
            try:
                self._await_free(time.monotonic() + release_timeout_s)
            except Exception:
                self._set(Phase.FAILED)
                raise
            self._set(Phase.EMPTY)
