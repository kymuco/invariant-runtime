"""Real OS subprocess supervision checks for the isolated research pilot."""

from __future__ import annotations

import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from supervision import Phase, RestartPolicy, WorkerSupervisor, WorkerUnavailable
from worker_supervisor import WorkerArtifact, WorkerStartupError, launch_worker, port_is_free


class SupervisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="invariant-supervision-pilot-")
        self.addCleanup(self.temp.cleanup)
        file = Path(self.temp.name) / "worker.py"
        source = Path(__file__).with_name("worker_fixture.py").read_text(encoding="utf-8")
        file.write_text(source.replace('ARTIFACT_REVISION = "template"', 'ARTIFACT_REVISION = "v1"'), encoding="utf-8")
        self.artifact = WorkerArtifact.pin(file, "v1")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.supervisors: list[WorkerSupervisor] = []
        self.addCleanup(self.cleanup_supervisors)

    def cleanup_supervisors(self) -> None:
        for supervisor in self.supervisors:
            supervisor.close()
        self.assertTrue(port_is_free(self.port), "worker leaked exclusive port")

    def new_supervisor(self, *, policy: RestartPolicy | None = None, launcher=None) -> WorkerSupervisor:
        supervisor = WorkerSupervisor(
            self.artifact, port=self.port,
            policy=policy or RestartPolicy(max_restarts=2, initial_delay_s=0.03, max_delay_s=0.1, poll_interval_s=0.01),
            launcher=launcher,
        )
        self.supervisors.append(supervisor)
        return supervisor

    @staticmethod
    def await_state(predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = predicate()
            if state:
                return state
            time.sleep(0.01)
        raise AssertionError("condition not met before deadline")

    def crash(self, supervisor: WorkerSupervisor) -> int:
        old_pid = supervisor.snapshot().pid
        self.assertIsNotNone(old_pid)
        with self.assertRaises((EOFError, RuntimeError, TimeoutError, OSError, BrokenPipeError)):
            supervisor.call("crash", timeout=0.7)
        self.await_state(lambda: not supervisor.snapshot().worker_alive)
        return old_pid  # type: ignore[return-value]

    def test_desired_and_observed_states_without_background_monitor(self):
        s = self.new_supervisor()
        self.assertEqual(s.snapshot().phase, Phase.STOPPED)
        s.enable()
        self.assertTrue(s.snapshot().worker_alive)
        self.assertEqual(s.call("ping")["revision"], "v1")
        old_pid = self.crash(s)
        snap = s.snapshot()
        self.assertEqual(snap.phase, Phase.RUNNING)  # not reconciled yet
        self.assertFalse(snap.worker_alive)
        self.assertEqual(snap.pid, old_pid)
        s.reconcile_once()
        self.assertEqual(s.snapshot().phase, Phase.BACKOFF)
        self.assertIn("unexpected_exit", [k for k, _ in s.events()])

    def test_monitor_automatically_detects_crash_and_restarts_new_process(self):
        s = self.new_supervisor()
        s.enable()
        s.start_monitor()
        pid1 = self.crash(s)
        self.await_state(lambda: s.snapshot().phase == Phase.RUNNING and s.snapshot().pid != pid1)
        self.assertEqual(s.snapshot().restarts_used, 1)
        self.assertEqual(s.call("ping")["revision"], "v1")
        self.assertNotEqual(s.snapshot().pid, pid1)
        self.assertEqual(len([k for k, _ in s.events() if k == "unexpected_exit"]), 1)

    def test_repeated_crash_exhausts_budget_and_does_not_restart_forever(self):
        s = self.new_supervisor()
        s.enable()
        s.start_monitor()
        for count in (1, 2):
            old = self.crash(s)
            self.await_state(lambda: s.snapshot().phase == Phase.RUNNING and s.snapshot().pid != old)
            self.assertEqual(s.snapshot().restarts_used, count)
        self.crash(s)
        self.await_state(lambda: s.snapshot().phase == Phase.EXHAUSTED)
        self.assertEqual(s.snapshot().restarts_used, 2)
        self.assertTrue(s.snapshot().desired_running)
        self.assertIsNone(s.snapshot().pid)
        with self.assertRaises(WorkerUnavailable):
            s.call("ping")
        time.sleep(0.12)
        self.assertEqual(s.snapshot().phase, Phase.EXHAUSTED)
        self.assertEqual(len([k for k, _ in s.events() if k == "running"]), 3)

    def test_manual_disable_enable_resets_only_exhausted_session(self):
        p = RestartPolicy(max_restarts=0, initial_delay_s=0.02, max_delay_s=0.05, poll_interval_s=0.01)
        s = self.new_supervisor(policy=p)
        s.enable()
        s.start_monitor()
        self.crash(s)
        self.await_state(lambda: s.snapshot().phase == Phase.EXHAUSTED)
        s.disable()
        self.assertEqual(s.snapshot().phase, Phase.STOPPED)
        self.assertFalse(s.snapshot().desired_running)
        s.enable()
        self.assertEqual(s.snapshot().restarts_used, 0)
        self.assertEqual(s.snapshot().phase, Phase.RUNNING)
        self.assertEqual(s.call("ping")["revision"], "v1")

    def test_explicit_disable_during_backoff_prevents_resurrection(self):
        p = RestartPolicy(max_restarts=3, initial_delay_s=0.35, max_delay_s=0.4, poll_interval_s=0.01)
        s = self.new_supervisor(policy=p)
        s.enable()
        s.start_monitor()
        self.crash(s)
        self.await_state(lambda: s.snapshot().phase == Phase.BACKOFF)
        s.disable()
        time.sleep(0.45)
        self.assertEqual(s.snapshot().phase, Phase.STOPPED)
        self.assertIsNone(s.snapshot().pid)
        self.assertFalse(s.snapshot().desired_running)

    def test_resource_contention_defers_restarts_without_double_binding(self):
        p = RestartPolicy(max_restarts=2, initial_delay_s=0.25, max_delay_s=0.3, poll_interval_s=0.01)
        s = self.new_supervisor(policy=p)
        s.enable()
        s.start_monitor()
        self.crash(s)
        self.await_state(lambda: s.snapshot().phase == Phase.BACKOFF)
        contender = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            contender.bind(("127.0.0.1", self.port))
            contender.listen(1)
            self.await_state(lambda: s.snapshot().phase == Phase.RESOURCE_WAIT)
            self.assertEqual(s.snapshot().restarts_used, 0)
            self.assertIsNone(s.snapshot().pid)
        finally:
            contender.close()
        self.await_state(lambda: s.snapshot().phase == Phase.RUNNING)
        self.assertEqual(s.snapshot().restarts_used, 1)
        self.assertEqual(s.call("ping")["revision"], "v1")

    def test_broken_restart_retries_boundedly_and_exhausts(self):
        calls = [0]
        def launcher(artifact, port):
            calls[0] += 1
            if calls[0] > 1:
                raise WorkerStartupError("synthetic retry failure")
            return launch_worker(artifact, port=port)
        s = self.new_supervisor(launcher=launcher)
        s.enable()
        s.start_monitor()
        self.crash(s)
        self.await_state(lambda: s.snapshot().phase == Phase.EXHAUSTED)
        self.assertEqual(calls[0], 3)
        self.assertEqual(s.snapshot().restarts_used, 2)
        self.assertIn("synthetic retry failure", s.snapshot().last_error)
        self.assertTrue(port_is_free(self.port))

    def test_unverified_launcher_exception_fails_closed(self):
        calls = [0]
        def launcher(artifact, port):
            calls[0] += 1
            if calls[0] > 1:
                raise ValueError("unknown ownership from launcher")
            return launch_worker(artifact, port=port)
        s = self.new_supervisor(launcher=launcher)
        s.enable()
        s.start_monitor()
        self.crash(s)
        self.await_state(lambda: s.snapshot().phase == Phase.FAULTED)
        self.assertEqual(calls[0], 2)
        time.sleep(0.1)
        self.assertEqual(calls[0], 2)
        with self.assertRaises(WorkerUnavailable):
            s.call("ping")

    def test_artifact_mutation_is_not_silently_loaded_on_restart(self):
        s = self.new_supervisor()
        s.enable()
        s.start_monitor()
        with self.artifact.path.open("a", encoding="utf-8") as fp:
            fp.write("\n# mutated artifact\n")
        self.crash(s)
        self.await_state(lambda: s.snapshot().phase == Phase.EXHAUSTED)
        self.assertEqual(s.snapshot().restarts_used, 2)
        self.assertTrue(port_is_free(self.port))
        self.assertIn("artifact bytes changed", s.snapshot().last_error)

    def test_monitor_idempotent_and_close_reaps_child(self):
        s = self.new_supervisor()
        s.enable()
        s.start_monitor()
        s.start_monitor()
        old_pid = s.snapshot().pid
        s.close()
        self.assertTrue(port_is_free(self.port))
        self.assertEqual(s.snapshot().phase, Phase.STOPPED)
        self.assertEqual(s.snapshot().desired_running, False)
        self.assertIsNone(s.snapshot().pid)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            s.enable()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            s.start_monitor()
        self.assertIsNotNone(old_pid)

    def test_concurrent_manual_reconciliations_do_not_double_allocate(self):
        p = RestartPolicy(max_restarts=1, initial_delay_s=0.05, max_delay_s=0.08, poll_interval_s=0.02)
        s = self.new_supervisor(policy=p)
        s.enable()
        self.crash(s)
        s.reconcile_once()
        time.sleep(0.06)
        errors = []
        def attempt():
            try:
                s.reconcile_once()
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join(4)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertEqual(s.snapshot().restarts_used, 1)
        self.assertEqual(s.snapshot().phase, Phase.RUNNING)
        self.assertEqual(len([k for k, _ in s.events() if k == "running"]), 2)

    def test_no_replay_of_inflight_failed_operation(self):
        s = self.new_supervisor()
        s.enable()
        s.start_monitor()
        old = self.crash(s)
        self.await_state(lambda: s.snapshot().phase == Phase.RUNNING and s.snapshot().pid != old)
        self.assertEqual(s.call("ping")["kind"], "pong")
        self.assertEqual(s.snapshot().restarts_used, 1)
        # Crash op is not replayed on fresh worker. Were it replayed,
        # the new worker would have exited too.
        time.sleep(0.08)
        self.assertTrue(s.snapshot().worker_alive)

    def test_bad_policy_is_rejected_before_any_side_effect(self):
        for kwargs in ({"max_restarts": -1}, {"max_restarts": 101},
                       {"initial_delay_s": 0}, {"max_delay_s": 0.01, "initial_delay_s": 0.2},
                       {"poll_interval_s": -1}):
            with self.assertRaises(ValueError):
                RestartPolicy(**kwargs)
        self.assertTrue(port_is_free(self.port))


if __name__ == "__main__":
    unittest.main()
