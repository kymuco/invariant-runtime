"""Subprocess-backed research adapter for StopFirstSlot, not a public API.

Only stdlib. Owns every spawned process, bounds readiness/shutdown waits and
verifies immutable artifact bytes. This fixture is *not* a security sandbox.
"""

from __future__ import annotations

import hashlib
import json
import queue
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from threading import Thread


class WorkerStartupError(RuntimeError):
    """Startup or validation did not produce an acceptable ready worker."""


class WorkerShutdownError(RuntimeError):
    """Worker liveness cannot be proved terminated after escalation."""


def port_is_free(port: int) -> bool:
    """Actual OS bind probe; serves as our simulated exclusive GPU lease."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


@dataclass(frozen=True, slots=True)
class WorkerArtifact:
    path: Path
    revision: str
    sha256: str

    @classmethod
    def pin(cls, path: Path, revision: str) -> WorkerArtifact:
        path = path.resolve()
        return cls(path, revision, hashlib.sha256(path.read_bytes()).hexdigest())


@dataclass(frozen=True, slots=True)
class StopReport:
    method: str  # already_exited | graceful | terminated | killed
    exit_code: int


class WorkerHandle:
    def __init__(self, proc: subprocess.Popen[str], output: queue.Queue[dict | None], *, revision: str):
        self.proc = proc
        self._output = output
        self.revision = revision
        self.last_stop: StopReport | None = None

    @property
    def pid(self) -> int:
        return self.proc.pid

    def wait_message(self, timeout: float) -> dict:
        try:
            message = self._output.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError("worker response deadline reached") from exc
        if message is None:
            raise EOFError("worker exited without response")
        return message

    def request(self, operation: str, *, timeout: float = 1.0) -> dict:
        if self.proc.poll() is not None:
            raise RuntimeError("worker exited")
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps({"op": operation}) + "\n")
        self.proc.stdin.flush()
        return self.wait_message(timeout)

    def stop(self, *, graceful_timeout: float = 0.9, escalation_timeout: float = 0.8) -> StopReport:
        """Graceful stop, then terminate, then kill; always wait/reap the child."""
        if self.last_stop is not None:
            return self.last_stop
        if self.proc.poll() is not None:
            self.last_stop = StopReport("already_exited", self.proc.wait())
            self._close_stdin()
            return self.last_stop
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write('{"op":"stop"}\n')
            self.proc.stdin.flush()
        except (OSError, BrokenPipeError):
            pass
        try:
            code = self.proc.wait(timeout=graceful_timeout)
            self.last_stop = StopReport("graceful", code)
        except subprocess.TimeoutExpired:
            self.last_stop = self._escalate(timeout=escalation_timeout)
        finally:
            self._close_stdin()
        return self.last_stop

    def _escalate(self, *, timeout: float) -> StopReport:
        if self.proc.poll() is None:
            try:
                self.proc.terminate()
            except ProcessLookupError:
                pass
        try:
            return StopReport("terminated", self.proc.wait(timeout=timeout))
        except subprocess.TimeoutExpired:
            if self.proc.poll() is None:
                try:
                    self.proc.kill()
                except ProcessLookupError:
                    pass
            try:
                return StopReport("killed", self.proc.wait(timeout=timeout))
            except subprocess.TimeoutExpired as exc:
                raise WorkerShutdownError("worker failed to exit after terminate and kill") from exc

    def _close_stdin(self) -> None:
        if self.proc.stdin is not None and not self.proc.stdin.closed:
            try:
                self.proc.stdin.close()
            except OSError:
                pass


def launch_worker(
    artifact: WorkerArtifact,
    *,
    port: int,
    mode: str = "normal",
    startup_timeout: float = 3.0,
    stop_timeout: float = 0.9,
) -> WorkerHandle:
    """Launch pinned code; never reuse a live interpreter or mutate sys.modules."""
    current_hash = hashlib.sha256(artifact.path.read_bytes()).hexdigest()
    if current_hash != artifact.sha256:
        raise WorkerStartupError("artifact bytes changed after pinning")

    proc = subprocess.Popen(
        [sys.executable, "-u", str(artifact.path), "--port", str(port), "--mode", mode],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        bufsize=1,
        cwd=str(artifact.path.parent),
    )
    output: queue.Queue[dict | None] = queue.Queue()

    def reader() -> None:
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                try:
                    output.put(json.loads(line))
                except ValueError:
                    output.put({"kind": "error", "error": "invalid worker JSON"})
        finally:
            output.put(None)
            proc.stdout.close()

    Thread(target=reader, daemon=True, name=f"worker-reader-{proc.pid}").start()
    handle = WorkerHandle(proc, output, revision=artifact.revision)
    try:
        message = handle.wait_message(startup_timeout)
        if (
            message.get("kind") != "ready"
            or message.get("revision") != artifact.revision
            or message.get("port") != port
            or message.get("pid") != proc.pid
            or proc.poll() is not None
        ):
            raise WorkerStartupError(f"worker did not become ready: {message!r}")
        return handle
    except (TimeoutError, EOFError, WorkerStartupError) as exc:
        # Critical: a failed startup may already hold the exclusive resource.
        # Stop/reap it before exposing failure to generation coordinator.
        try:
            handle.stop(graceful_timeout=stop_timeout)
        except WorkerShutdownError as cleanup_exc:
            raise WorkerStartupError("worker startup failed and process could not be reaped") from cleanup_exc
        raise WorkerStartupError("worker startup failed; subprocess reaped") from exc
