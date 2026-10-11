"""Real-subprocess, stdlib-only tests for heartbeat and RPC deadline research."""
from __future__ import annotations

import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from health_probe import HealthPolicy, HealthSupervisor, OperationLost, Phase, WorkerUnavailable
from worker_supervisor import WorkerArtifact, port_is_free


class HealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="invariant-health-pilot-")
        self.addCleanup(self.temp.cleanup)
        code = Path(self.temp.name) / "health_worker.py"
        code.write_text(Path(__file__).with_name("health_fixture.py").read_text(encoding="utf-8")
                        .replace('REVISION = "template"', 'REVISION = "v1"'), encoding="utf-8")
        self.artifact = WorkerArtifact.pin(code, "v1")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.supervisors: list[HealthSupervisor] = []
        self.addCleanup(self.cleanup_supervisors)

    def cleanup_supervisors(self) -> None:
        for s in self.supervisors:
            s.close()
        self.assertTrue(port_is_free(self.port), "exclusive resource leaked")

    def make(self, **kwargs) -> HealthSupervisor:
        policy = HealthPolicy(heartbeat_interval_s=0.03, heartbeat_timeout_s=0.28,
                              poll_interval_s=0.015, startup_timeout_s=3,
                              stop_timeout_s=0.12, restart_delay_s=kwargs.get("restart_delay_s", 0.05),
                              max_restarts=kwargs.get("max_restarts", 2))
        s = HealthSupervisor(self.artifact, port=self.port, policy=policy)
        self.supervisors.append(s)
        return s

    def await_state(self, condition, timeout=4):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            result = condition()
            if result:
                return result
            time.sleep(0.01)
        self.fail("desired condition not reached")

    def test_initial_health_and_idle_heartbeat(self):
        s = self.make()
        s.enable()
        s.start_monitor()
        time.sleep(0.35)  # greater than heartbeat timeout, no false positives
        self.assertEqual(s.snapshot().phase, Phase.READY)
        self.assertEqual(s.snapshot().restarts_used, 0)
        self.assertEqual(s.call("ping")["value"], "pong")

    def test_long_but_valid_rpc_is_not_confused_with_hang(self):
        s = self.make()
        s.enable()
        s.start_monitor()
        result = s.call("slow", deadline_s=0.9, duration=0.43)
        self.assertEqual(result["value"], "slow_ok")
        self.assertEqual(s.snapshot().phase, Phase.READY)
        self.assertEqual(s.snapshot().restarts_used, 0)

    def test_missing_heartbeat_with_live_worker_triggers_recovery(self):
        s = self.make()
        s.enable()
        s.start_monitor()
        pid = s.snapshot().pid
        self.assertEqual(s.call("silence")["value"], "silenced")
        self.await_state(lambda: "heartbeat_timeout" in s.events())
        self.await_state(lambda: s.snapshot().phase == Phase.READY and s.snapshot().pid != pid)
        self.assertEqual(s.snapshot().restarts_used, 1)
        self.assertEqual(s.call("ping")["value"], "pong")

    def test_hung_rpc_with_live_heartbeat_fails_deadline_and_recovers(self):
        s = self.make()
        s.enable()
        s.start_monitor()
        pid = s.snapshot().pid
        with self.assertRaises(OperationLost):
            s.call("hang", deadline_s=0.34)
        self.await_state(lambda: "rpc_deadline" in s.events())
        self.await_state(lambda: s.snapshot().phase == Phase.READY and s.snapshot().pid != pid)
        self.assertNotIn("heartbeat_timeout", s.events())
        self.assertEqual(s.call("ping")["value"], "pong")

    def test_hung_call_does_not_block_supervisor_disable(self):
        s = self.make()
        s.enable()
        s.start_monitor()
        errors: list[BaseException] = []
        started = threading.Event()

        def call_hang():
            started.set()
            try:
                s.call("hang", deadline_s=1.5)
            except BaseException as exc:
                errors.append(exc)

        t = threading.Thread(target=call_hang)
        t.start()
        self.assertTrue(started.wait(1))
        time.sleep(0.07)
        begin = time.monotonic()
        s.disable()
        t.join(timeout=2)
        self.assertFalse(t.is_alive())
        self.assertLess(time.monotonic() - begin, 1.5)
        self.assertFalse(s.snapshot().desired_running)
        self.assertEqual(s.snapshot().phase, Phase.STOPPED)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], OperationLost)
        time.sleep(0.14)
        self.assertIsNone(s.snapshot().pid)

    def test_repeated_health_failures_exhaust_restart_budget(self):
        s = self.make(max_restarts=1)
        s.enable()
        s.start_monitor()
        s.call("silence")
        self.await_state(lambda: s.snapshot().phase == Phase.READY and s.snapshot().restarts_used == 1)
        s.call("silence")
        self.await_state(lambda: s.snapshot().phase == Phase.EXHAUSTED)
        self.assertEqual(s.snapshot().restarts_used, 1)
        with self.assertRaises(WorkerUnavailable):
            s.call("ping")
        time.sleep(0.2)
        self.assertEqual(s.snapshot().phase, Phase.EXHAUSTED)
        self.assertTrue(port_is_free(self.port))

    def test_zero_restart_budget_and_explicit_reset(self):
        s = self.make(max_restarts=0)
        s.enable()
        s.start_monitor()
        s.call("silence")
        self.await_state(lambda: s.snapshot().phase == Phase.EXHAUSTED)
        s.disable()
        s.enable()
        self.assertEqual(s.snapshot().phase, Phase.READY)
        self.assertEqual(s.snapshot().restarts_used, 0)

    def test_unexpected_process_exit_is_not_replayed(self):
        s = self.make()
        s.enable()
        s.start_monitor()
        old_pid = s.snapshot().pid
        with self.assertRaises(OperationLost):
            s.call("crash", deadline_s=0.7)
        self.await_state(lambda: s.snapshot().phase == Phase.READY and s.snapshot().pid != old_pid)
        self.assertEqual(s.snapshot().restarts_used, 1)
        self.assertEqual(s.call("ping")["value"], "pong")

    def test_competing_owner_defers_restart(self):
        s = self.make(restart_delay_s=0.3)
        s.enable()
        s.start_monitor()
        s.call("silence")
        self.await_state(lambda: s.snapshot().phase == Phase.BACKOFF)
        contender = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            contender.bind(("127.0.0.1", self.port))
            contender.listen(1)
            self.await_state(lambda: s.snapshot().phase == Phase.RESOURCE_WAIT)
            self.assertEqual(s.snapshot().restarts_used, 0)
        finally:
            contender.close()
        self.await_state(lambda: s.snapshot().phase == Phase.READY)
        self.assertEqual(s.snapshot().restarts_used, 1)

    def test_artifact_mutation_prevents_restart(self):
        s = self.make(max_restarts=1)
        s.enable()
        s.start_monitor()
        with self.artifact.path.open("a", encoding="utf-8") as fp:
            fp.write("\n# modified after pin\n")
        s.call("silence")
        self.await_state(lambda: s.snapshot().phase == Phase.EXHAUSTED)
        self.assertIn("pinned artifact mutated", s.snapshot().last_error or "")
        self.assertTrue(port_is_free(self.port))

    def test_failed_process_stop_retains_owned_worker_handle(self):
        from unittest.mock import patch

        s = self.make()
        s.enable()
        worker = s._worker  # Research-only fault injection; not public API.
        self.assertIsNotNone(worker)
        assert worker is not None
        self.assertEqual(s.call("silence")["value"], "silenced")
        time.sleep(s.policy.heartbeat_timeout_s + 0.04)
        with patch.object(worker, "stop", side_effect=RuntimeError("stop failed")):
            s.tick()
        self.assertEqual(s.snapshot().phase, Phase.FAULTED)
        self.assertEqual(s.snapshot().pid, worker.proc.pid)
        self.assertTrue(s.snapshot().process_alive)
        self.assertFalse(port_is_free(self.port))
        s.disable()  # Recovered stop callback frees the actual process.
        self.assertTrue(port_is_free(self.port))

    def test_policy_validation(self):
        for options in ({"max_restarts": -1}, {"max_restarts": True},
                        {"heartbeat_interval_s": 0}, {"heartbeat_timeout_s": 0.01},
                        {"poll_interval_s": float("nan")}, {"stop_timeout_s": -1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                HealthPolicy(**options)


if __name__ == "__main__":
    unittest.main()
