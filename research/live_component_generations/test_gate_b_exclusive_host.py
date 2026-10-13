"""Gate B.2: stop-first and bounded leases with real Invariant plugin lifecycle.

Tests use a filesystem claim as a fake exclusive device: initialization is
forbidden while another real plugin instance holds the claim. This is NOT
physical GPU/VRAM release evidence.
"""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from catalog_epoch import StalePlan
from gate_b_exclusive_host import RealStopFirstHost
from prototype import ProviderUnavailable
from stop_first import CandidateFailed, DrainTimeout, Phase, UnsafeRelease


class ExclusiveRealPluginTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="invariant-exclusive-real-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.claim = self.root / "one-device.claim"
        self.events = self.root / "lifecycle.log"
        self.write_version("1.0.0")

    def write_version(
        self,
        version: str,
        *,
        fail_before: bool = False,
        fail_after: bool = False,
        leaks_resource: bool = False,
        stop_stuck: bool = False,
    ):
        lex = self.root / f"lex-{version}"
        tts = self.root / f"tts-{version}"
        lex.mkdir(exist_ok=True)
        tts.mkdir(exist_ok=True)
        (lex / "plugin.py").write_text(
            "from invariant.spec import BasePlugin\n"
            "class Lexicon(BasePlugin):\n"
            "    def initialize(self, **deps): self.closed = False\n"
            "    def shutdown(self): self.closed = True\n"
            f"    def convert(self, text): return 'lex-{version}:' + text\n",
            encoding="utf-8",
        )
        (lex / "manifest.json").write_text(json.dumps({
            "manifest_version": 1, "id": "research:lex", "version": version,
            "source": {"code_path": "plugin.py", "class_name": "Lexicon"},
        }), encoding="utf-8")

        init = (
            "    def initialize(self, lexicon):\n"
            "        from pathlib import Path\n"
            "        self.lexicon = lexicon\n"
            "        self.closed = False\n"
            f"        self.resource_file = Path({str(self.claim)!r})\n"
            f"        with Path({str(self.events)!r}).open('a', encoding='utf-8') as log: log.write('try:{version}\\n')\n"
        )
        if fail_before:
            init += "        raise RuntimeError('candidate startup failed before allocate')\n"
        else:
            init += (
                "        if self.resource_file.exists():\n"
                "            raise RuntimeError('double allocation detected')\n"
                f"        self.resource_file.write_text({version!r}, encoding='utf-8')\n"
            )
            if fail_after:
                init += "        raise RuntimeError('candidate startup failed after allocate')\n"

        stop = (
            "    def shutdown(self):\n"
            "        from pathlib import Path\n"
            f"        with Path({str(self.events)!r}).open('a', encoding='utf-8') as log: log.write('stop:{version}\\n')\n"
            "        self.closed = True\n"
        )
        if not stop_stuck and not leaks_resource:
            stop += (
                "        if hasattr(self, 'resource_file'):\n"
                "            self.resource_file.unlink(missing_ok=True)\n"
            )

        (tts / "plugin.py").write_text(
            "from invariant.spec import BasePlugin\n"
            "class TTS(BasePlugin):\n"
            + init + stop
            + f"    def synthesize(self, text): return 'tts-{version}:' + self.lexicon.convert(text)\n",
            encoding="utf-8",
        )
        (tts / "manifest.json").write_text(json.dumps({
            "manifest_version": 1, "id": "research:tts", "version": version,
            "source": {"code_path": "plugin.py", "class_name": "TTS"},
            "dependencies": [{
                "id": f"research:lex@{version}", "param": "lexicon",
                "scope": "EXECUTION",
            }],
        }), encoding="utf-8")

    def host(self) -> RealStopFirstHost:
        h = RealStopFirstHost(
            [self.root], root_ref="research:tts",
            is_resource_free=lambda: not self.claim.exists(),
        )

        def cleanup():
            try:
                h.close(drain_timeout_s=0, release_timeout_s=0)
            except (UnsafeRelease, RuntimeError):
                pass
            self.claim.unlink(missing_ok=True)

        self.addCleanup(cleanup)
        return h

    def stage_v2(self, h: RealStopFirstHost, **kwargs):
        self.write_version("2.0.0", **kwargs)
        return h.stage()

    def test_real_stop_old_before_start_new_and_single_owner(self):
        h = self.host()
        self.assertEqual(self.claim.read_text(encoding="utf-8"), "1.0.0")
        initial = h.snapshot()
        view = h.replace(self.stage_v2(h))
        self.assertEqual(view.epoch, 2)
        self.assertEqual(initial.epoch, 1)
        self.assertEqual(self.claim.read_text(encoding="utf-8"), "2.0.0")
        self.assertEqual(self.events.read_text(encoding="utf-8").splitlines(), [
            "try:1.0.0", "stop:1.0.0", "try:2.0.0",
        ])
        self.assertEqual(h.call("ok"), "tts-2.0.0:lex-2.0.0:ok")
        self.assertEqual(h.status().phase, Phase.ACTIVE)

    def test_drain_fences_admission_until_existing_use_returns(self):
        h = self.host()
        route = None
        with h._slot.acquire() as borrowed:
            route = borrowed
        old = route.plugin
        original = old.synthesize
        entered = threading.Event()
        resume = threading.Event()
        results = []
        errors = []

        def blocked(text):
            entered.set()
            if not resume.wait(timeout=5):
                raise TimeoutError("old caller was not released")
            return original(text)

        old.synthesize = blocked
        stage = self.stage_v2(h)

        def caller():
            try:
                results.append(h.call("before"))
            except BaseException as exc:
                errors.append(exc)

        def update():
            try:
                h.replace(stage, drain_timeout_s=2)
            except BaseException as exc:
                errors.append(exc)

        user = threading.Thread(target=caller)
        user.start()
        self.assertTrue(entered.wait(timeout=5))
        updater = threading.Thread(target=update)
        updater.start()
        try:
            end = time.monotonic() + 3
            while h.status().phase is not Phase.DRAINING and time.monotonic() < end:
                time.sleep(0.002)
            self.assertEqual(h.status().phase, Phase.DRAINING)
            with self.assertRaises(ProviderUnavailable):
                h.call("not-admitted")
            self.assertEqual(self.claim.read_text(encoding="utf-8"), "1.0.0")
        finally:
            resume.set()
            updater.join(timeout=5)
            user.join(timeout=5)
        self.assertFalse(updater.is_alive() or user.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, ["tts-1.0.0:lex-1.0.0:before"])
        self.assertEqual(h.call("after"), "tts-2.0.0:lex-2.0.0:after")

    def test_drain_timeout_keeps_old_plugin_operational(self):
        h = self.host()
        stage = self.stage_v2(h)
        with h._slot.acquire():
            with self.assertRaises(DrainTimeout):
                h.replace(stage, drain_timeout_s=0)
            self.assertEqual(h.status().phase, Phase.ACTIVE)
            self.assertEqual(self.claim.read_text(encoding="utf-8"), "1.0.0")
        self.assertEqual(h.call("still"), "tts-1.0.0:lex-1.0.0:still")

    def test_absent_release_proof_fences_candidate_start(self):
        self.write_version("1.0.0", stop_stuck=True)
        h = self.host()
        stage = self.stage_v2(h)
        with self.assertRaises(UnsafeRelease):
            h.replace(stage, release_timeout_s=0)
        self.assertEqual(h.status().phase, Phase.FAILED)
        self.assertEqual(self.claim.read_text(encoding="utf-8"), "1.0.0")
        self.assertEqual(self.events.read_text(encoding="utf-8").splitlines(), [
            "try:1.0.0", "stop:1.0.0",
        ])
        with self.assertRaises(ProviderUnavailable):
            h.call("blocked")
        with self.assertRaisesRegex(RuntimeError, "manual recovery"):
            h.replace(stage)

    def test_startup_failure_before_allocation_restores_old_version(self):
        h = self.host()
        stage = self.stage_v2(h, fail_before=True)
        with self.assertRaises(CandidateFailed) as error:
            h.replace(stage)
        self.assertTrue(error.exception.restored)
        self.assertEqual(h.status().phase, Phase.ACTIVE)
        self.assertEqual(h.snapshot().epoch, 1)
        self.assertEqual(self.claim.read_text(encoding="utf-8"), "1.0.0")
        self.assertEqual(h.call("restored"), "tts-1.0.0:lex-1.0.0:restored")

    def test_failed_candidate_cleanup_allows_explicit_restore(self):
        h = self.host()
        stage = self.stage_v2(h, fail_after=True)
        with self.assertRaises(CandidateFailed) as error:
            h.replace(stage)
        self.assertTrue(error.exception.restored)
        self.assertEqual(h.status().phase, Phase.ACTIVE)
        self.assertEqual(self.claim.read_text(encoding="utf-8"), "1.0.0")

    def test_partial_candidate_resource_leak_fails_closed_no_restore(self):
        h = self.host()
        stage = self.stage_v2(h, fail_after=True, leaks_resource=True)
        with self.assertRaises(UnsafeRelease):
            h.replace(stage, release_timeout_s=0)
        self.assertEqual(h.status().phase, Phase.FAILED)
        self.assertEqual(self.claim.read_text(encoding="utf-8"), "2.0.0")
        with self.assertRaises(ProviderUnavailable):
            h.call("unsafe")

    def test_changed_staged_source_rejected_before_old_stop(self):
        h = self.host()
        stage = self.stage_v2(h)
        with (self.root / "tts-2.0.0" / "plugin.py").open("a", encoding="utf-8") as f:
            f.write("\n# mutation between stage and exclusive replace\n")
        with self.assertRaises(StalePlan):
            h.replace(stage)
        self.assertEqual(h.status().phase, Phase.ACTIVE)
        self.assertEqual(h.snapshot().epoch, 1)
        self.assertEqual(self.claim.read_text(encoding="utf-8"), "1.0.0")

    def test_stale_stage_cannot_reactivate_superseded_version(self):
        h = self.host()
        first = self.stage_v2(h)
        stale = h.stage()
        h.replace(first)
        with self.assertRaises(StalePlan):
            h.replace(stale)
        self.assertEqual(h.call("new"), "tts-2.0.0:lex-2.0.0:new")

    def test_final_close_drains_and_confirms_release(self):
        h = self.host()
        staged = self.stage_v2(h)  # metadata only; no candidate allocation
        self.assertTrue(self.claim.exists())
        h.close()
        self.assertFalse(self.claim.exists())
        self.assertEqual(h.status().phase, Phase.EMPTY)
        h.close()
        with self.assertRaises(ProviderUnavailable):
            h.call("closed")
        with self.assertRaisesRegex(RuntimeError, "closed"):
            h.replace(staged)

    def test_close_drain_timeout_never_interrupts_borrower(self):
        h = self.host()
        with h._slot.acquire():
            with self.assertRaises(DrainTimeout):
                h.close(drain_timeout_s=0)
            self.assertEqual(h.status().phase, Phase.ACTIVE)
            self.assertTrue(self.claim.exists())
        self.assertEqual(h.call("working"), "tts-1.0.0:lex-1.0.0:working")
        h.close()
        self.assertFalse(self.claim.exists())

    def test_failed_close_release_proof_is_not_reported_as_clean(self):
        self.write_version("1.0.0", stop_stuck=True)
        h = self.host()
        with self.assertRaises(UnsafeRelease):
            h.close(release_timeout_s=0)
        self.assertEqual(h.status().phase, Phase.FAILED)
        self.assertTrue(self.claim.exists())
        with self.assertRaises(ProviderUnavailable):
            h.call("fenced")


if __name__ == "__main__":
    unittest.main()
