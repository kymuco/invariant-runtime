"""End-to-end stdlib subprocess checks for the research stop-first slot."""

from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from prototype import ProviderSpec, ProviderUnavailable
from stop_first import CandidateFailed, Phase, StopFirstSlot
from worker_supervisor import WorkerArtifact, WorkerHandle, WorkerStartupError, launch_worker, port_is_free


class WorkerLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.TemporaryDirectory(prefix="invariant-worker-pilot-")
        self.addCleanup(self.root.cleanup)
        self.port = self._free_port()
        self.slot = StopFirstSlot("speech.synthesize@1", is_resource_free=lambda: port_is_free(self.port))
        self.handles: list[WorkerHandle] = []
        self.stops: list[tuple[str, str]] = []
        self.artifacts: dict[str, WorkerArtifact] = {}
        source = Path(__file__).with_name("worker_fixture.py").read_text(encoding="utf-8")
        self.assertIn('ARTIFACT_REVISION = "template"', source)
        for rev in ("v1", "v2", "v3"):
            directory = Path(self.root.name) / rev
            directory.mkdir()
            code = directory / "worker.py"
            code.write_text(source.replace('ARTIFACT_REVISION = "template"', f'ARTIFACT_REVISION = "{rev}"'), encoding="utf-8")
            self.artifacts[rev] = WorkerArtifact.pin(code, rev)
        self.addCleanup(self._cleanup_handles)

    def _cleanup_handles(self) -> None:
        for handle in self.handles:
            handle.stop(graceful_timeout=0.9)
        self.assertTrue(port_is_free(self.port), "worker leaked exclusive port")

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def add(self, revision: str, *, mode: str = "normal", start=None) -> None:
        def starter() -> WorkerHandle:
            handle = (start or (lambda: launch_worker(self.artifacts[revision], port=self.port, mode=mode)))()
            self.handles.append(handle)
            return handle

        def stopper(handle: WorkerHandle) -> None:
            report = handle.stop(graceful_timeout=0.9)
            self.stops.append((revision, report.method))

        self.slot.install(ProviderSpec(self.slot.contract, "example:tts", revision, starter, stopper))

    def test_staging_is_cold_and_artifact_versions_are_distinct(self) -> None:
        self.add("v1")
        self.add("v2")
        self.assertEqual(self.handles, [])
        self.assertEqual(self.slot.snapshot().phase, Phase.EMPTY)
        self.assertNotEqual(self.artifacts["v1"].sha256, self.artifacts["v2"].sha256)
        self.assertTrue(port_is_free(self.port))

    def test_actual_process_replacement_preserves_host_and_exclusivity(self) -> None:
        host_pid = os.getpid()
        self.add("v1")
        self.add("v2")
        self.slot.replace("example:tts", "v1")
        with self.slot.acquire() as h1:
            pid1 = h1.pid
            self.assertNotEqual(pid1, host_pid)
            self.assertEqual(h1.request("ping")["revision"], "v1")
            self.assertFalse(port_is_free(self.port))
        self.slot.replace("example:tts", "v2")
        with self.slot.acquire() as h2:
            self.assertNotEqual(h2.pid, pid1)
            self.assertNotEqual(h2.pid, host_pid)
            self.assertEqual(h2.request("ping")["revision"], "v2")
            self.assertFalse(port_is_free(self.port))
        self.assertEqual(self.handles[0].proc.poll(), 0)
        self.assertEqual(self.stops, [("v1", "graceful")])
        self.assertEqual(os.getpid(), host_pid)

    def test_real_lease_drains_before_worker_shutdown(self) -> None:
        self.add("v1")
        self.add("v2")
        self.slot.replace("example:tts", "v1")
        entered = threading.Event()
        release = threading.Event()
        errors: list[BaseException] = []

        def borrow() -> None:
            try:
                with self.slot.acquire() as h:
                    self.assertEqual(h.request("ping")["revision"], "v1")
                    entered.set()
                    if not release.wait(timeout=3):
                        raise TimeoutError("test failed to release lease")
                    self.assertIsNone(h.proc.poll())
            except BaseException as exc:
                errors.append(exc)

        def upgrade() -> None:
            try:
                self.slot.replace("example:tts", "v2", drain_timeout_s=2)
            except BaseException as exc:
                errors.append(exc)

        borrower = threading.Thread(target=borrow)
        updater = threading.Thread(target=upgrade)
        borrower.start()
        self.assertTrue(entered.wait(2))
        updater.start()
        try:
            deadline = time.monotonic() + 2
            while self.slot.snapshot().phase != Phase.DRAINING and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(self.slot.snapshot().phase, Phase.DRAINING)
            self.assertFalse(port_is_free(self.port))
            with self.assertRaises(ProviderUnavailable):
                with self.slot.acquire():
                    pass
        finally:
            release.set()
            borrower.join(timeout=3)
            updater.join(timeout=3)
        self.assertFalse(borrower.is_alive() or updater.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.slot.snapshot().active, ("example:tts", "v2"))
        self.assertEqual(self.stops, [("v1", "graceful")])

    def test_candidate_error_before_binding_restores_previous_worker(self) -> None:
        self.add("v1")
        self.add("v2", mode="fail_before_bind")
        self.slot.replace("example:tts", "v1")
        old = self.handles[-1]
        with self.assertRaises(CandidateFailed) as cm:
            self.slot.replace("example:tts", "v2")
        self.assertTrue(cm.exception.restored)
        with self.slot.acquire() as restored:
            self.assertNotEqual(old.pid, restored.pid)
            self.assertEqual(restored.request("ping")["revision"], "v1")
        self.assertEqual(old.proc.poll(), 0)
        self.assertEqual(self.slot.snapshot().phase, Phase.ACTIVE)

    def test_candidate_error_after_binding_is_reaped_before_restore(self) -> None:
        self.add("v1")
        self.add("v2", mode="fail_after_bind")
        self.slot.replace("example:tts", "v1")
        with self.assertRaises(CandidateFailed) as cm:
            self.slot.replace("example:tts", "v2", release_timeout_s=1)
        self.assertTrue(cm.exception.restored)
        self.assertEqual(self.slot.snapshot().active, ("example:tts", "v1"))
        with self.slot.acquire() as restored:
            self.assertEqual(restored.request("ping")["revision"], "v1")

    def test_worker_readiness_timeout_kills_staged_process_and_restores(self) -> None:
        self.add("v1")
        self.add("v2", start=lambda: launch_worker(self.artifacts["v2"], port=self.port, mode="hang_ready", startup_timeout=0.07))
        self.slot.replace("example:tts", "v1")
        with self.assertRaises(CandidateFailed) as cm:
            self.slot.replace("example:tts", "v2")
        self.assertTrue(cm.exception.restored)
        with self.slot.acquire() as h:
            self.assertEqual(h.request("ping")["revision"], "v1")

    def test_noncooperative_worker_stop_escalates_before_new_version(self) -> None:
        self.add("v1", mode="hang_stop")
        self.add("v2")
        self.slot.replace("example:tts", "v1")
        old = self.handles[-1]
        self.slot.replace("example:tts", "v2")
        self.assertIsNotNone(old.proc.poll())
        self.assertIn(self.stops[0][1], ("terminated", "killed"))
        with self.slot.acquire() as h:
            self.assertEqual(h.request("ping")["revision"], "v2")

    @unittest.skipIf(os.name == "nt", "Windows terminate() is uncatchable; SIGTERM ignore is POSIX-only")
    def test_worker_ignoring_sigterm_is_killed_and_port_released(self) -> None:
        self.add("v1", mode="ignore_terminate")
        self.add("v2")
        self.slot.replace("example:tts", "v1")
        old = self.handles[0]
        self.slot.replace("example:tts", "v2")
        self.assertIsNotNone(old.proc.poll())
        self.assertEqual(self.stops, [("v1", "killed")])
        with self.slot.acquire() as h:
            self.assertEqual(h.request("ping")["revision"], "v2")

    def test_external_competitor_cannot_bind_exclusive_resource(self) -> None:
        self.add("v1")
        self.slot.replace("example:tts", "v1")
        with self.assertRaises(WorkerStartupError):
            launch_worker(self.artifacts["v2"], port=self.port)
        with self.slot.acquire() as worker:
            self.assertEqual(worker.request("ping")["revision"], "v1")

    def test_modified_staged_artifact_refused_without_disrupting_live_worker(self) -> None:
        self.add("v1")
        self.add("v2")
        self.slot.replace("example:tts", "v1")
        with self.artifacts["v2"].path.open("a", encoding="utf-8") as file:
            file.write("\n# mutation after staging\n")
        with self.assertRaises(CandidateFailed) as cm:
            self.slot.replace("example:tts", "v2")
        self.assertTrue(cm.exception.restored)
        with self.slot.acquire() as worker:
            self.assertEqual(worker.request("ping")["revision"], "v1")

    def test_worker_crash_releases_resource_but_requires_explicit_reconcile(self) -> None:
        self.add("v1")
        self.add("v2")
        self.slot.replace("example:tts", "v1")
        with self.slot.acquire() as worker:
            try:
                worker.request("crash", timeout=0.5)
            except (EOFError, RuntimeError, TimeoutError):
                pass
            worker.proc.wait(timeout=1)
            self.assertEqual(worker.proc.returncode, 17)
        self.assertTrue(port_is_free(self.port))
        # Research gap: StopFirstSlot doesn't monitor exited workers; the
        # control-plane snapshot can remain ACTIVE with a dead handle.
        self.assertEqual(self.slot.snapshot().active, ("example:tts", "v1"))
        self.slot.replace("example:tts", "v2")
        with self.slot.acquire() as replacement:
            self.assertEqual(replacement.request("ping")["revision"], "v2")

    def test_successful_old_worker_shutdown_releases_os_resource(self) -> None:
        self.add("v1")
        self.slot.replace("example:tts", "v1")
        handle = self.handles[0]
        self.assertFalse(port_is_free(self.port))
        result = handle.stop()
        self.assertEqual(result.method, "graceful")
        self.assertTrue(port_is_free(self.port))
        self.assertEqual(handle.proc.poll(), 0)


if __name__ == "__main__":
    unittest.main()
