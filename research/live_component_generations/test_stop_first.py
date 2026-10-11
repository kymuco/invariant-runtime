"""Local, stdlib-only stop-first hypothesis checks."""

from __future__ import annotations

import threading
import time
import unittest

from prototype import ProviderSpec, ProviderUnavailable
from stop_first import (
    CandidateFailed,
    DrainTimeout,
    Phase,
    StopFailed,
    StopFirstSlot,
    UnsafeRelease,
)


class Exclusive:
    def __init__(self) -> None:
        self.owner: str | None = None
        self.actions: list[str] = []

    def start(self, name: str) -> str:
        if self.owner is not None:
            raise RuntimeError("double allocation")
        self.owner = name
        self.actions.append("start:" + name)
        return name

    def stop(self, name: object) -> None:
        if self.owner != name:
            raise RuntimeError("wrong owner")
        self.actions.append("stop:" + str(name))
        self.owner = None


class StopFirstTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resource = Exclusive()
        self.slot = StopFirstSlot("speech.synthesize@1", is_resource_free=lambda: self.resource.owner is None)

    def add(self, name: str, *, start=None, stop=None) -> None:
        self.slot.install(
            ProviderSpec(
                self.slot.contract,
                name,
                "1",
                start or (lambda: self.resource.start(name)),
                stop or self.resource.stop,
            )
        )

    def test_install_does_not_allocate_and_on_demand_start(self) -> None:
        self.add("a")
        self.add("b")
        self.assertIsNone(self.resource.owner)
        with self.assertRaises(ProviderUnavailable):
            with self.slot.acquire():
                pass
        self.assertTrue(self.slot.replace("a", "1"))
        self.assertEqual(self.slot.snapshot().active, ("a", "1"))
        self.assertFalse(self.slot.replace("a", "1"))
        self.assertEqual(self.resource.actions, ["start:a"])

    def test_strictly_stop_old_before_starting_new(self) -> None:
        self.add("a")
        self.add("b")
        self.slot.replace("a", "1")
        self.slot.replace("b", "1")
        self.assertEqual(self.resource.actions, ["start:a", "stop:a", "start:b"])
        with self.slot.acquire() as active:
            self.assertEqual(active, "b")

    def test_existing_lease_drains_and_new_admission_is_denied(self) -> None:
        self.add("a")
        self.add("b")
        self.slot.replace("a", "1")
        entered = threading.Event()
        release = threading.Event()
        errors: list[Exception] = []

        def old_use() -> None:
            with self.slot.acquire() as instance:
                self.assertEqual(instance, "a")
                entered.set()
                release.wait(timeout=3)

        def upgrade() -> None:
            try:
                self.slot.replace("b", "1", drain_timeout_s=2)
            except Exception as exc:
                errors.append(exc)

        user = threading.Thread(target=old_use)
        user.start()
        self.assertTrue(entered.wait(2))
        worker = threading.Thread(target=upgrade)
        worker.start()
        try:
            end = time.monotonic() + 2
            while self.slot.snapshot().phase is not Phase.DRAINING and time.monotonic() < end:
                time.sleep(0.002)
            self.assertEqual(self.slot.snapshot().phase, Phase.DRAINING)
            self.assertEqual(self.resource.actions, ["start:a"])
            with self.assertRaises(ProviderUnavailable):
                with self.slot.acquire():
                    pass
        finally:
            release.set()
            worker.join(3)
            user.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(user.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.resource.actions, ["start:a", "stop:a", "start:b"])

    def test_drain_timeout_restores_admission_without_stopping(self) -> None:
        self.add("a")
        self.add("b")
        self.slot.replace("a", "1")
        with self.slot.acquire():
            with self.assertRaises(DrainTimeout):
                self.slot.replace("b", "1", drain_timeout_s=0)
            self.assertEqual(self.slot.snapshot().phase, Phase.ACTIVE)
            self.assertEqual(self.resource.actions, ["start:a"])
        with self.slot.acquire() as value:
            self.assertEqual(value, "a")

    def test_new_start_failure_restores_old_after_resource_freed(self) -> None:
        self.add("a")
        self.add("bad", start=lambda: (_ for _ in ()).throw(RuntimeError("load fails")))
        self.slot.replace("a", "1")
        with self.assertRaises(CandidateFailed) as cm:
            self.slot.replace("bad", "1")
        self.assertTrue(cm.exception.restored)
        self.assertEqual(self.resource.actions, ["start:a", "stop:a", "start:a"])
        self.assertEqual(self.slot.snapshot().active, ("a", "1"))

    def test_failed_candidate_holding_gpu_blocks_unsafe_restore(self) -> None:
        self.add("a")

        def partial_start() -> str:
            self.resource.start("bad")
            raise RuntimeError("partial initialization")

        self.add("bad", start=partial_start)
        self.slot.replace("a", "1")
        with self.assertRaises(UnsafeRelease):
            self.slot.replace("bad", "1", release_timeout_s=0)
        self.assertEqual(self.slot.snapshot().phase, Phase.FAILED)
        self.assertEqual(self.resource.owner, "bad")
        self.assertEqual(self.resource.actions, ["start:a", "stop:a", "start:bad"])
        with self.assertRaises(ProviderUnavailable):
            with self.slot.acquire():
                pass

    def test_stopper_error_fences_slot_and_skips_candidate_start(self) -> None:
        self.add("a", stop=lambda _instance: (_ for _ in ()).throw(RuntimeError("worker stuck")))
        self.add("b")
        self.slot.replace("a", "1")
        with self.assertRaises(StopFailed):
            self.slot.replace("b", "1")
        self.assertEqual(self.resource.actions, ["start:a"])
        self.assertEqual(self.slot.snapshot().phase, Phase.FAILED)
        with self.assertRaisesRegex(RuntimeError, "manual recovery"):
            self.slot.replace("b", "1")

    def test_release_probe_refuses_new_start_if_old_stop_lies(self) -> None:
        self.add("a", stop=lambda _instance: None)
        self.add("b")
        self.slot.replace("a", "1")
        with self.assertRaises(UnsafeRelease):
            self.slot.replace("b", "1", release_timeout_s=0)
        self.assertEqual(self.resource.actions, ["start:a"])
        self.assertEqual(self.slot.snapshot().phase, Phase.FAILED)

    def test_delayed_exclusive_release_is_observed_before_new_start(self) -> None:
        self.add("a", stop=lambda _instance: self.resource.actions.append("stop:a:pending"))
        self.add("b")
        self.slot.replace("a", "1")
        began = threading.Event()

        def delayed_cleanup() -> None:
            began.set()
            time.sleep(0.03)
            self.resource.owner = None

        releaser = threading.Thread(target=delayed_cleanup)
        releaser.start()
        try:
            self.assertTrue(began.wait(1))
            self.slot.replace("b", "1", release_timeout_s=0.5)
        finally:
            releaser.join(1)
        self.assertEqual(self.resource.actions, ["start:a", "stop:a:pending", "start:b"])
        self.assertEqual(self.slot.snapshot().phase, Phase.ACTIVE)

    def test_consumer_exception_releases_lease(self) -> None:
        self.add("a")
        self.add("b")
        self.slot.replace("a", "1")
        with self.assertRaisesRegex(ValueError, "consumer error"):
            with self.slot.acquire():
                raise ValueError("consumer error")
        self.assertEqual(self.slot.snapshot().users, 0)
        self.slot.replace("b", "1", drain_timeout_s=0)
        self.assertEqual(self.resource.actions, ["start:a", "stop:a", "start:b"])

    def test_restore_failure_fences_slot(self) -> None:
        starts = [0]

        def old_start() -> str:
            starts[0] += 1
            if starts[0] > 1:
                raise RuntimeError("old is not restorable")
            return self.resource.start("a")

        self.add("a", start=old_start)
        self.add("bad", start=lambda: (_ for _ in ()).throw(RuntimeError("candidate invalid")))
        self.slot.replace("a", "1")
        with self.assertRaises(CandidateFailed) as cm:
            self.slot.replace("bad", "1")
        self.assertFalse(cm.exception.restored)
        self.assertEqual(self.slot.snapshot().phase, Phase.FAILED)

    def test_no_current_generation_and_bad_start_remains_empty(self) -> None:
        self.add("bad", start=lambda: (_ for _ in ()).throw(RuntimeError("bad")))
        with self.assertRaises(CandidateFailed) as cm:
            self.slot.replace("bad", "1")
        self.assertFalse(cm.exception.restored)
        self.assertEqual(self.slot.snapshot().phase, Phase.EMPTY)

    def test_replacement_can_return_to_prior_version_on_demand(self) -> None:
        self.add("a")
        self.add("b")
        self.slot.replace("a", "1")
        self.slot.replace("b", "1")
        self.slot.replace("a", "1")
        self.assertEqual(self.resource.actions, ["start:a", "stop:a", "start:b", "stop:b", "start:a"])

    def test_invalid_parameters_have_no_effect(self) -> None:
        self.add("a")
        self.slot.replace("a", "1")
        with self.assertRaises(ValueError):
            self.slot.replace("a", "1", drain_timeout_s=-1)
        with self.assertRaises(KeyError):
            self.slot.replace("unknown", "1")
        self.assertEqual(self.resource.actions, ["start:a"])

    def test_concurrent_replacements_serialize_and_never_overlap(self) -> None:
        self.add("a")
        self.add("b")
        self.add("c")
        self.slot.replace("a", "1")
        errors: list[Exception] = []
        barrier = threading.Barrier(3)

        def replace(name: str) -> None:
            barrier.wait(timeout=2)
            try:
                self.slot.replace(name, "1")
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=replace, args=("b",))
        t2 = threading.Thread(target=replace, args=("c",))
        t1.start()
        t2.start()
        barrier.wait(timeout=2)
        t1.join(timeout=3)
        t2.join(timeout=3)
        self.assertFalse(t1.is_alive() or t2.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.slot.snapshot().phase, Phase.ACTIVE)
        self.assertEqual(len([x for x in self.resource.actions if x.startswith("start:")]), 3)
        self.assertEqual(len([x for x in self.resource.actions if x.startswith("stop:")]), 2)


if __name__ == "__main__":
    unittest.main()
