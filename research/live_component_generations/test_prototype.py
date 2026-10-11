"""Executable hypothesis checks; standard-library unittest only."""

from __future__ import annotations

import threading
import unittest

from prototype import GenerationConflict, ProviderSlot, ProviderSpec, ProviderUnavailable


class BindingPilotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.slot = ProviderSlot("speech.synthesize@1")
        self.events: list[str] = []

    def add(self, name: str, revision: str = "1", *, start=None, stop=None) -> None:
        self.slot.install(
            ProviderSpec(
                contract=self.slot.contract,
                plugin_id=name,
                revision=revision,
                start=start or (lambda: self.events.append("start:" + name) or name),
                stop=stop or (lambda _value: self.events.append("stop:" + name)),
            )
        )

    def test_install_does_not_start_anything(self) -> None:
        self.add("a")
        self.add("b")
        self.assertEqual(self.slot.installed(), (("a", "1"), ("b", "1")))
        self.assertEqual(self.slot.active(), None)
        self.assertEqual(self.events, [])
        with self.assertRaises(ProviderUnavailable):
            with self.slot.acquire():
                pass

    def test_switch_pins_existing_operation_then_drains(self) -> None:
        self.add("a")
        self.add("b")
        self.slot.switch("a", "1")
        with self.slot.acquire() as old:
            self.assertEqual(old, "a")
            self.slot.switch("b", "1")
            self.assertNotIn("stop:a", self.events)
            with self.slot.acquire() as new:
                self.assertEqual(new, "b")
        self.assertEqual(self.events, ["start:a", "start:b", "stop:a"])
        self.assertEqual(self.slot.active(), ("b", "1"))
        self.assertFalse(self.slot.switch("b", "1"))
        self.slot.deactivate()
        self.assertEqual(self.events[-1], "stop:b")

    def test_start_failure_preserves_active(self) -> None:
        self.add("a")
        self.add("bad", start=lambda: (_ for _ in ()).throw(RuntimeError("no VRAM")))
        self.slot.switch("a", "1")
        with self.assertRaisesRegex(RuntimeError, "no VRAM"):
            self.slot.switch("bad", "1")
        self.assertEqual(self.slot.active(), ("a", "1"))
        with self.slot.acquire() as obj:
            self.assertEqual(obj, "a")
        self.assertEqual(self.events, ["start:a"])

    def test_immutable_identity_and_contract_check(self) -> None:
        self.add("a")
        with self.assertRaisesRegex(ValueError, "already installed"):
            self.add("a")
        with self.assertRaisesRegex(ValueError, "contract mismatch"):
            self.slot.install(ProviderSpec("vision.ocr@1", "x", "1", lambda: "x", lambda _: None))

    def test_deactivate_drains_before_stopping(self) -> None:
        self.add("a")
        self.slot.switch("a", "1")
        with self.slot.acquire():
            self.slot.deactivate()
            self.assertEqual(self.slot.active(), None)
            self.assertNotIn("stop:a", self.events)
            with self.assertRaises(ProviderUnavailable):
                with self.slot.acquire():
                    pass
        self.assertEqual(self.events, ["start:a", "stop:a"])

    def test_failed_stopper_is_observable_and_does_not_break_switch(self) -> None:
        self.add("a", stop=lambda _: (_ for _ in ()).throw(RuntimeError("cleanup failure")))
        self.add("b")
        self.slot.switch("a", "1")
        self.slot.switch("b", "1")
        self.assertEqual(self.slot.active(), ("b", "1"))
        self.assertIn("cleanup failure", str(self.slot.shutdown_errors()))

    def test_concurrent_switch_loser_is_disposed(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        errors: list[BaseException] = []

        def slow_start() -> str:
            entered.set()
            if not release.wait(timeout=3):
                raise TimeoutError("test coordination")
            return "slow"

        self.add("a")
        self.add("slow", start=slow_start)
        self.add("fast")
        self.slot.switch("a", "1")

        def attempt() -> None:
            try:
                self.slot.switch("slow", "1")
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=attempt)
        thread.start()
        try:
            self.assertTrue(entered.wait(timeout=3))
            self.slot.switch("fast", "1")
        finally:
            release.set()
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], GenerationConflict)
        self.assertEqual(self.slot.active(), ("fast", "1"))
        self.assertIn("stop:slow", self.events)

    def test_exclusive_resource_blocks_parallel_switch_without_damage(self) -> None:
        holder: list[str] = []

        def start(name: str) -> str:
            if holder:
                raise RuntimeError("resource busy")
            holder.append(name)
            return name

        def stop(_: object) -> None:
            holder.pop()

        self.add("a", start=lambda: start("a"), stop=stop)
        self.add("b", start=lambda: start("b"), stop=stop)
        self.slot.switch("a", "1")
        with self.assertRaisesRegex(RuntimeError, "resource busy"):
            self.slot.switch("b", "1")
        self.assertEqual(holder, ["a"])
        self.assertEqual(self.slot.active(), ("a", "1"))
        self.slot.deactivate()
        self.assertEqual(holder, [])

    def test_reactivate_stopped_version_starts_fresh_instance(self) -> None:
        self.add("a")
        self.add("b")
        self.slot.switch("a", "1")
        self.slot.switch("b", "1")
        self.slot.switch("a", "1")
        self.assertEqual(self.events, ["start:a", "start:b", "stop:a", "start:a", "stop:b"])

    def test_exception_during_borrow_releases_lease(self) -> None:
        self.add("a")
        self.slot.switch("a", "1")
        with self.assertRaisesRegex(ValueError, "consumer failed"):
            with self.slot.acquire():
                self.slot.deactivate()
                raise ValueError("consumer failed")
        self.assertEqual(self.events, ["start:a", "stop:a"])


if __name__ == "__main__":
    unittest.main()
