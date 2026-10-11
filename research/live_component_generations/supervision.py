"""Research-only supervisor for a single, version-pinned child worker.

Not an Invariant public API, production process manager, or security sandbox.
Only child-process exit is observed; unresponsive but living workers, descendant
processes, state migration, and arbitrary GPU allocations are out of scope.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from threading import Event, RLock, Thread, current_thread
from typing import Callable

from worker_supervisor import (
    WorkerArtifact,
    WorkerHandle,
    WorkerShutdownError,
    WorkerStartupError,
    launch_worker,
    port_is_free,
)


class Phase(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    BACKOFF = "backoff"
    RESOURCE_WAIT = "resource_wait"
    EXHAUSTED = "exhausted"
    STOPPING = "stopping"
    FAULTED = "faulted"


class WorkerUnavailable(RuntimeError):
    """The desired worker is not currently serving requests."""


@dataclass(frozen=True, slots=True)
class RestartPolicy:
    max_restarts: int = 2
    initial_delay_s: float = 0.05
    max_delay_s: float = 0.5
    poll_interval_s: float = 0.02

    def __post_init__(self) -> None:
        if not isinstance(self.max_restarts, int) or not 0 <= self.max_restarts <= 100:
            raise ValueError("max_restarts must be an integer in [0, 100]")
        if not 0 < self.initial_delay_s <= self.max_delay_s:
            raise ValueError("retry delays must be positive and ordered")
        if self.poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be positive")


@dataclass(frozen=True, slots=True)
class Snapshot:
    desired_running: bool
    phase: Phase
    revision: str
    pid: int | None
    worker_alive: bool
    restarts_used: int
    next_retry_at: float | None
    last_error: str | None


class WorkerSupervisor:
    """Desired/observed convergence for one OS child and one exclusive port.

    A session is started by enable() and ended by disable(). A single session
    has a bounded cumulative restart budget, NOT a rolling/infinite retry
    counter. Only explicit disable() followed by enable() resets that budget.

    All lifecycle operations serialize through one RLock. This is a deliberately
    simple research design: launch/stop can block control calls for their
    bounded worker timeouts, and callbacks must not re-enter the supervisor.
    """

    def __init__(
        self,
        artifact: WorkerArtifact,
        *,
        port: int,
        policy: RestartPolicy = RestartPolicy(),
        launcher: Callable[[WorkerArtifact, int], WorkerHandle] | None = None,
        resource_free: Callable[[int], bool] = port_is_free,
    ) -> None:
        self._artifact = artifact
        self._port = port
        self._policy = policy
        self._launcher = launcher or (lambda a, p: launch_worker(a, port=p))
        self._resource_free = resource_free
        self._lock = RLock()
        self._desired = False
        self._phase = Phase.STOPPED
        self._handle: WorkerHandle | None = None
        self._restarts_used = 0
        self._next_retry: float | None = None
        self._error: str | None = None
        self._events: list[tuple[str, str]] = []
        self._monitor_stop = Event()
        self._monitor: Thread | None = None
        self._closed = False

    def _set(self, phase: Phase, detail: str = "") -> None:
        self._phase = phase
        self._events.append((phase.value, detail))

    def snapshot(self) -> Snapshot:
        with self._lock:
            handle = self._handle
            return Snapshot(
                desired_running=self._desired,
                phase=self._phase,
                revision=self._artifact.revision,
                pid=handle.pid if handle is not None else None,
                worker_alive=handle is not None and handle.proc.poll() is None,
                restarts_used=self._restarts_used,
                next_retry_at=self._next_retry,
                last_error=self._error,
            )

    def events(self) -> tuple[tuple[str, str], ...]:
        with self._lock:
            return tuple(self._events)

    def _launch(self) -> None:
        self._set(Phase.STARTING)
        handle = self._launcher(self._artifact, self._port)
        # Startup validation in worker_supervisor checks READY and digest.
        # A child may crash between returning READY and this check.
        if handle.proc.poll() is not None:
            handle.stop()
            raise WorkerStartupError("ready worker exited immediately")
        self._handle = handle
        self._next_retry = None
        self._error = None
        self._set(Phase.RUNNING, f"pid={handle.pid}")

    def enable(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("supervisor is closed")
            if self._desired:
                raise RuntimeError("already enabled; disable before resetting restart budget")
            if self._handle is not None:
                raise RuntimeError("worker ownership unresolved")
            if not self._resource_free(self._port):
                raise WorkerUnavailable("exclusive port is occupied")
            self._desired = True
            self._restarts_used = 0
            self._error = None
            try:
                self._launch()
            except Exception as exc:
                # enable() failure does not enable background autonomous launch.
                self._desired = False
                self._error = repr(exc)
                self._set(Phase.FAULTED, "initial activation failed")
                raise

    def reconcile_once(self) -> Snapshot:
        """Advance desired/observed state one tick; safe to call concurrently."""
        with self._lock:
            if not self._desired or self._closed:
                return self.snapshot()
            if self._phase in (Phase.EXHAUSTED, Phase.FAULTED):
                return self.snapshot()
            handle = self._handle
            now = time.monotonic()
            if handle is not None:
                if handle.proc.poll() is None:
                    return self.snapshot()
                code = handle.proc.wait()  # reap observed-dead child
                handle.stop()  # close pipes; never performs a new launch
                self._handle = None
                self._error = f"unexpected child exit={code}, pid={handle.pid}"
                self._events.append(("unexpected_exit", self._error))
                if self._restarts_used >= self._policy.max_restarts:
                    self._set(Phase.EXHAUSTED, "restart budget exhausted")
                    return self.snapshot()
                self._next_retry = now + min(
                    self._policy.initial_delay_s * (2 ** self._restarts_used),
                    self._policy.max_delay_s,
                )
                self._set(Phase.BACKOFF, "waiting before restart")
                return self.snapshot()

            if self._phase not in (Phase.BACKOFF, Phase.RESOURCE_WAIT):
                return self.snapshot()
            if self._next_retry is not None and now < self._next_retry:
                return self.snapshot()
            if not self._resource_free(self._port):
                # Do not consume attempts while a resource is unavailable.
                self._next_retry = now + self._policy.poll_interval_s
                if self._phase != Phase.RESOURCE_WAIT:
                    self._set(Phase.RESOURCE_WAIT, "resource still occupied")
                return self.snapshot()
            if self._restarts_used >= self._policy.max_restarts:
                self._set(Phase.EXHAUSTED, "restart budget exhausted")
                return self.snapshot()
            self._restarts_used += 1
            try:
                self._launch()
            except WorkerStartupError as exc:
                # The launcher contract must ensure a failed child was reaped.
                self._error = repr(exc)
                if not self._resource_free(self._port):
                    self._set(Phase.FAULTED, "failed startup retained exclusive resource")
                elif self._restarts_used >= self._policy.max_restarts:
                    self._set(Phase.EXHAUSTED, "restart budget exhausted")
                else:
                    self._next_retry = time.monotonic() + min(
                        self._policy.initial_delay_s * (2 ** self._restarts_used),
                        self._policy.max_delay_s,
                    )
                    self._set(Phase.BACKOFF, "candidate restart failed")
            except Exception as exc:
                # Unexpected launchers may not fulfill child cleanup contract.
                self._error = repr(exc)
                self._set(Phase.FAULTED, "unverified launcher failure")
            return self.snapshot()

    def call(self, operation: str, *, timeout: float = 1.0) -> dict:
        """A controlled, serialized RPC; never replays a failed operation."""
        with self._lock:
            if not self._desired or self._phase is not Phase.RUNNING or self._handle is None:
                raise WorkerUnavailable("worker is not ready")
            if self._handle.proc.poll() is not None:
                raise WorkerUnavailable("worker exited; waiting for reconciliation")
            return self._handle.request(operation, timeout=timeout)

    def disable(self) -> None:
        """Explicit host intent removes the desire to run, preventing respawn."""
        with self._lock:
            self._desired = False
            self._next_retry = None
            handle = self._handle
            if handle is not None:
                self._set(Phase.STOPPING)
                try:
                    handle.stop()
                except WorkerShutdownError as exc:
                    self._error = repr(exc)
                    self._set(Phase.FAULTED, "could not prove process termination")
                    raise
                self._handle = None
            self._set(Phase.STOPPED)

    def start_monitor(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("supervisor is closed")
            if self._monitor is not None:
                return
            self._monitor_stop.clear()
            thread = Thread(target=self._monitor_main, daemon=True, name="research-worker-reconciler")
            self._monitor = thread
            thread.start()

    def _monitor_main(self) -> None:
        while not self._monitor_stop.wait(self._policy.poll_interval_s):
            try:
                self.reconcile_once()
            except Exception as exc:
                with self._lock:
                    self._error = repr(exc)
                    self._set(Phase.FAULTED, "monitor error: manual reconciliation required")
                return

    def close(self) -> None:
        self._monitor_stop.set()
        try:
            self.disable()
        finally:
            with self._lock:
                self._closed = True
                monitor = self._monitor
            if monitor is not None and monitor is not current_thread():
                monitor.join(timeout=2.0)
                if monitor.is_alive():
                    raise RuntimeError("monitor thread did not stop")
