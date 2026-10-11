"""Research-only heartbeat/deadline supervisor for one child worker.

The worker emits heartbeats on a dedicated logical control path independent of
its request loop. A heartbeat is process-liveness evidence, NOT proof that a
user operation completed. Lost RPCs are never replayed. Not a public API.
"""
from __future__ import annotations

import hashlib
import json
import queue
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from worker_supervisor import WorkerArtifact, port_is_free


class WorkerUnavailable(RuntimeError):
    pass


class OperationLost(RuntimeError):
    pass


class Phase(str, Enum):
    STOPPED = "stopped"
    READY = "ready"
    BACKOFF = "backoff"
    RESOURCE_WAIT = "resource_wait"
    EXHAUSTED = "exhausted"
    FAULTED = "faulted"


@dataclass(frozen=True)
class HealthPolicy:
    heartbeat_interval_s: float = 0.04
    heartbeat_timeout_s: float = 0.45
    poll_interval_s: float = 0.02
    startup_timeout_s: float = 3.0
    stop_timeout_s: float = 0.3
    restart_delay_s: float = 0.06
    max_restarts: int = 2

    def __post_init__(self) -> None:
        values = (self.heartbeat_interval_s, self.heartbeat_timeout_s,
                  self.poll_interval_s, self.startup_timeout_s,
                  self.stop_timeout_s, self.restart_delay_s)
        if any(v <= 0 or not float("-inf") < v < float("inf") for v in values):
            raise ValueError("all intervals must be finite and positive")
        if self.heartbeat_timeout_s <= 2 * self.heartbeat_interval_s:
            raise ValueError("heartbeat timeout must exceed twice the interval")
        if isinstance(self.max_restarts, bool) or not isinstance(self.max_restarts, int) or not 0 <= self.max_restarts <= 100:
            raise ValueError("max_restarts must be an integer in [0, 100]")


@dataclass(frozen=True)
class Snapshot:
    phase: Phase
    desired_running: bool
    pid: int | None
    process_alive: bool
    heartbeat_age_s: float | None
    in_flight: bool
    restarts_used: int
    last_error: str | None


class _Handle:
    def __init__(self, proc: subprocess.Popen[str]):
        self.proc = proc
        self.replies: queue.Queue[dict | None] = queue.Queue()
        self.lock = threading.Lock()
        self.call_lock = threading.Lock()
        self.last_beat = time.monotonic()
        self.in_flight_until: float | None = None
        self.poisoned: str | None = None
        threading.Thread(target=self._read, name=f"health-reader-{proc.pid}", daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        try:
            for line in self.proc.stdout:
                try:
                    frame = json.loads(line)
                except (ValueError, TypeError):
                    self.replies.put({"kind": "invalid"})
                    continue
                if frame.get("kind") == "heartbeat":
                    if frame.get("pid") == self.proc.pid:
                        with self.lock:
                            self.last_beat = time.monotonic()
                else:
                    self.replies.put(frame)
        finally:
            self.replies.put(None)
            self.proc.stdout.close()

    def health(self) -> tuple[float, bool, bool, str | None]:
        with self.lock:
            now = time.monotonic()
            overdue = self.in_flight_until is not None and now >= self.in_flight_until
            return now - self.last_beat, self.in_flight_until is not None, overdue, self.poisoned

    def send(self, message: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def call(self, operation: str, *, deadline_s: float, **params: object) -> dict:
        if deadline_s <= 0:
            raise ValueError("deadline must be positive")
        with self.call_lock:
            with self.lock:
                if self.poisoned or self.proc.poll() is not None:
                    raise WorkerUnavailable("worker has lost health or exited")
                deadline = time.monotonic() + deadline_s
                self.in_flight_until = deadline
            request_id = uuid.uuid4().hex
            try:
                self.send({"op": operation, "id": request_id, **params})
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        with self.lock:
                            self.poisoned = "rpc_deadline"
                        raise OperationLost("RPC deadline exceeded; outcome unknown")
                    try:
                        response = self.replies.get(timeout=min(remaining, 0.05))
                    except queue.Empty:
                        if self.proc.poll() is not None:
                            raise OperationLost("worker exited during RPC; outcome unknown")
                        continue
                    if response is None:
                        raise OperationLost("worker pipe closed during RPC; outcome unknown")
                    if response.get("kind") != "reply" or response.get("id") != request_id:
                        with self.lock:
                            self.poisoned = "protocol_error"
                        raise OperationLost("unexpected RPC response; outcome unknown")
                    with self.lock:
                        if self.poisoned:
                            raise OperationLost("worker invalidated during RPC; outcome unknown")
                    return response
            except (OSError, BrokenPipeError) as exc:
                raise OperationLost("worker transport failed; outcome unknown") from exc
            finally:
                with self.lock:
                    self.in_flight_until = None

    def stop(self, timeout_s: float) -> None:
        if self.proc.poll() is None:
            try:
                self.send({"op": "stop", "id": "shutdown"})
            except (OSError, BrokenPipeError):
                pass
            try:
                self.proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=timeout_s)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=timeout_s)
        else:
            self.proc.wait()
        if self.proc.stdin is not None and not self.proc.stdin.closed:
            self.proc.stdin.close()


def _launch(artifact: WorkerArtifact, port: int, policy: HealthPolicy) -> _Handle:
    if hashlib.sha256(artifact.path.read_bytes()).hexdigest() != artifact.sha256:
        raise RuntimeError("pinned artifact mutated")
    proc = subprocess.Popen(
        [sys.executable, "-u", str(artifact.path), "--port", str(port),
         "--heartbeat-interval", str(policy.heartbeat_interval_s)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", bufsize=1, cwd=str(artifact.path.parent),
    )
    handle = _Handle(proc)
    try:
        frame = handle.replies.get(timeout=policy.startup_timeout_s)
        if frame is None or frame.get("kind") != "ready" or frame.get("pid") != proc.pid or frame.get("port") != port or frame.get("revision") != artifact.revision or proc.poll() is not None:
            raise RuntimeError(f"readiness failed: {frame!r}")
        # A slow interpreter boot must not count as a missing heartbeat.
        with handle.lock:
            handle.last_beat = time.monotonic()
        return handle
    except Exception:
        handle.stop(policy.stop_timeout_s)
        raise


class HealthSupervisor:
    """One-worker desired-state reconciler; host calls remain separate from its lock."""
    def __init__(self, artifact: WorkerArtifact, *, port: int, policy: HealthPolicy = HealthPolicy()):
        self.artifact = artifact
        self.port = port
        self.policy = policy
        self._lock = threading.RLock()
        self._worker: _Handle | None = None
        self._phase = Phase.STOPPED
        self._desired = False
        self._used = 0
        self._retry_at = 0.0
        self._error: str | None = None
        self._events: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False

    def snapshot(self) -> Snapshot:
        with self._lock:
            h = self._worker
            age, busy, _, _ = h.health() if h is not None else (None, False, False, None)
            return Snapshot(self._phase, self._desired, h.proc.pid if h else None,
                            h.proc.poll() is None if h else False, age, busy,
                            self._used, self._error)

    def events(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._events)

    def _set(self, phase: Phase, event: str) -> None:
        self._phase = phase
        self._events.append(event)

    def enable(self) -> None:
        with self._lock:
            if self._closed or self._desired or self._worker is not None or self._phase is not Phase.STOPPED:
                raise RuntimeError("cannot enable without a clean stopped state")
            if not port_is_free(self.port):
                raise WorkerUnavailable("exclusive resource already occupied")
            self._desired = True
            self._used = 0
            try:
                self._worker = _launch(self.artifact, self.port, self.policy)
            except Exception:
                self._desired = False
                self._set(Phase.FAULTED, "initial_start_failed")
                raise
            self._error = None
            self._set(Phase.READY, "ready")

    def call(self, operation: str, *, deadline_s: float = 1.0, **params: object) -> dict:
        with self._lock:
            worker = self._worker
            if not self._desired or self._phase is not Phase.READY or worker is None:
                raise WorkerUnavailable("provider is not ready")
        return worker.call(operation, deadline_s=deadline_s, **params)

    def tick(self) -> Snapshot:
        with self._lock:
            if not self._desired or self._closed or self._phase in (Phase.FAULTED, Phase.EXHAUSTED):
                return self.snapshot()
            worker = self._worker
            now = time.monotonic()
            if worker is not None:
                age, _, overdue, poisoned = worker.health()
                reason = ("process_exit" if worker.proc.poll() is not None else
                          "rpc_deadline" if overdue or poisoned == "rpc_deadline" else
                          "heartbeat_timeout" if age >= self.policy.heartbeat_timeout_s else
                          "protocol_error" if poisoned else None)
                if reason is None:
                    return self.snapshot()
                with worker.lock:
                    worker.poisoned = reason
                self._error = reason
                self._events.append(reason)
                try:
                    worker.stop(self.policy.stop_timeout_s)
                except Exception as exc:
                    # Preserve the handle when process termination is unproven.
                    self._error = f"stop_unproven: {exc!r}"
                    self._set(Phase.FAULTED, "stop_unproven")
                    return self.snapshot()
                self._worker = None
                if not port_is_free(self.port):
                    self._set(Phase.FAULTED, "resource_not_released")
                    return self.snapshot()
                if self._used >= self.policy.max_restarts:
                    self._set(Phase.EXHAUSTED, "restart_budget_exhausted")
                    return self.snapshot()
                self._retry_at = now + self.policy.restart_delay_s * 2 ** self._used
                self._set(Phase.BACKOFF, "backoff")
                return self.snapshot()
            if self._phase not in (Phase.BACKOFF, Phase.RESOURCE_WAIT) or now < self._retry_at:
                return self.snapshot()
            if not port_is_free(self.port):
                self._set(Phase.RESOURCE_WAIT, "resource_wait")
                self._retry_at = now + self.policy.poll_interval_s
                return self.snapshot()
            if self._used >= self.policy.max_restarts:
                self._set(Phase.EXHAUSTED, "restart_budget_exhausted")
                return self.snapshot()
            self._used += 1
            try:
                self._worker = _launch(self.artifact, self.port, self.policy)
            except Exception as exc:
                self._error = f"start_failed: {exc!r}"
                if not port_is_free(self.port):
                    self._set(Phase.FAULTED, "resource_not_released")
                elif self._used >= self.policy.max_restarts:
                    self._set(Phase.EXHAUSTED, "restart_budget_exhausted")
                else:
                    self._retry_at = time.monotonic() + self.policy.restart_delay_s * 2 ** self._used
                    self._set(Phase.BACKOFF, "restart_failed")
            else:
                self._error = None
                self._set(Phase.READY, "recovered")
            return self.snapshot()

    def start_monitor(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("closed")
            if self._thread is not None:
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._watch, daemon=True, name="research-health-watch")
            self._thread.start()

    def _watch(self) -> None:
        while not self._stop.wait(self.policy.poll_interval_s):
            try:
                self.tick()
            except Exception as exc:
                with self._lock:
                    self._error = repr(exc)
                    self._set(Phase.FAULTED, "monitor_error")
                return

    def disable(self) -> None:
        with self._lock:
            self._desired = False
            worker = self._worker
            if worker is not None:
                try:
                    worker.stop(self.policy.stop_timeout_s)
                except Exception as exc:
                    # Retain ownership for diagnosis; do not lose a live child.
                    self._error = repr(exc)
                    self._set(Phase.FAULTED, "stop_unproven")
                    raise
                self._worker = None
            self._set(Phase.STOPPED, "disabled")

    def close(self) -> None:
        self._stop.set()
        try:
            self.disable()
        finally:
            with self._lock:
                self._closed = True
                thread = self._thread
            if thread and thread is not threading.current_thread():
                thread.join(timeout=2)
                if thread.is_alive():
                    raise RuntimeError("health monitor did not stop")
