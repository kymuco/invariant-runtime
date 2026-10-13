"""Gate B.1: real Invariant host activation with scoped operation leases.

Run separately from the Gate A characterizations. New tests are authored
on a research branch and have no core/runtime production effects.
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from catalog_epoch import StalePlan
from gate_b_leased_host import LeasedReadinessHost


class LeasedHostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="invariant-gate-b-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.write_version("1.0.0")

    def write_version(self, version: str, *, fail: bool = False):
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
        initializer = (
            "    def initialize(self, lexicon): raise RuntimeError('candidate startup failed')\n"
            if fail else
            "    def initialize(self, lexicon): self.lexicon = lexicon; self.closed = False\n"
        )
        (tts / "plugin.py").write_text(
            "from invariant.spec import BasePlugin\n"
            "class TTS(BasePlugin):\n"
            + initializer
            + "    def shutdown(self): self.closed = True\n"
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

    def host(self):
        h = LeasedReadinessHost([self.root], root_ref="research:tts")
        self.addCleanup(h.close)
        return h

    def upgrade(self, host):
        self.write_version("2.0.0")
        return host.prepare(host.stage())

    def test_unleased_old_generation_shuts_down_after_publish(self):
        host = self.host()
        old = host._active.plugin
        before = host.snapshot()
        new = host.publish_ready(self.upgrade(host))
        self.assertEqual(before.epoch, 1)
        self.assertEqual(new.epoch, 2)
        self.assertTrue(old.closed)
        self.assertTrue(old.lexicon.closed)
        self.assertEqual(host.live_generations(), (("research:tts@2.0.0", 0, False),))
        self.assertEqual(host.call("next"), "tts-2.0.0:lex-2.0.0:next")

    def test_inflight_call_holds_generation_until_method_returns(self):
        host = self.host()
        old = host._active.plugin
        original = old.synthesize
        entered = threading.Event()
        release = threading.Event()
        results = []

        def blocked(text):
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("old call not released")
            return original(text)

        old.synthesize = blocked

        def worker():
            try:
                results.append(host.call("before"))
            except BaseException as exc:
                results.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(timeout=5))
            new = host.publish_ready(self.upgrade(host))
            self.assertEqual(new.epoch, 2)
            self.assertFalse(old.closed)
            self.assertEqual(host.call("after"), "tts-2.0.0:lex-2.0.0:after")
            self.assertIn(("research:tts@1.0.0", 1, True), host.live_generations())
        finally:
            release.set()
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(results, ["tts-1.0.0:lex-1.0.0:before"])
        self.assertTrue(old.closed)
        self.assertTrue(old.lexicon.closed)
        self.assertEqual(len(host.live_generations()), 1)
        self.assertEqual(host.shutdown_errors(), ())

    def test_two_leases_require_last_release_before_cleanup(self):
        host = self.host()
        old = host._active.plugin
        with host.acquire() as first:
            with host.acquire() as second:
                self.assertIs(first, second)
                host.publish_ready(self.upgrade(host))
                self.assertFalse(old.closed)
                self.assertIn(("research:tts@1.0.0", 2, True), host.live_generations())
            self.assertFalse(old.closed)
            self.assertIn(("research:tts@1.0.0", 1, True), host.live_generations())
        self.assertTrue(old.closed)
        self.assertEqual(len(host.live_generations()), 1)

    def test_failed_call_still_releases_retired_lease(self):
        host = self.host()
        old = host._active.plugin
        with self.assertRaisesRegex(RuntimeError, "test failure"):
            with host.acquire() as plugin:
                self.write_version("2.0.0")
                host.publish_ready(host.prepare(host.stage()))
                self.assertFalse(old.closed)
                self.assertIs(old, plugin)
                raise RuntimeError("test failure")
        self.assertTrue(old.closed)
        self.assertEqual(len(host.live_generations()), 1)

    def test_close_rejects_admission_but_waits_for_existing_lease(self):
        host = self.host()
        old = host._active.plugin
        with host.acquire() as plugin:
            self.assertIs(plugin, old)
            host.close()
            self.assertFalse(old.closed)
            self.assertIn(("research:tts@1.0.0", 1, True), host.live_generations())
            with self.assertRaisesRegex(RuntimeError, "closed"):
                host.call("must-fail")
        self.assertTrue(old.closed)
        self.assertTrue(old.lexicon.closed)
        self.assertEqual(host.live_generations(), ())
        host.close()

    def test_close_retires_active_and_pending_ready_candidates(self):
        host = self.host()
        current = host._active.plugin
        pending = self.upgrade(host)
        host.close()
        self.assertEqual(pending.status, "discarded")
        self.assertTrue(pending.plugin.closed)
        self.assertTrue(current.closed)
        self.assertEqual(host.live_generations(), ())
        self.assertEqual(host.shutdown_errors(), ())

    def test_stale_prepared_cas_aborts_candidate_without_stopping_current(self):
        host = self.host()
        self.write_version("2.0.0")
        first_stage, stale_stage = host.stage(), host.stage()
        first = host.prepare(first_stage)
        stale = host.prepare(stale_stage)
        host.publish_ready(first)
        with self.assertRaises(StalePlan):
            host.publish_ready(stale)
        self.assertEqual(stale.status, "discarded")
        self.assertTrue(stale.plugin.closed)
        self.assertEqual(host.call("live"), "tts-2.0.0:lex-2.0.0:live")
        self.assertEqual(host.shutdown_errors(), ())

    def test_broken_prepare_leaves_current_route_and_leases_intact(self):
        host = self.host()
        old = host._active.plugin
        with host.acquire():
            self.write_version("2.0.0", fail=True)
            with self.assertRaisesRegex(RuntimeError, "candidate startup failed"):
                host.prepare(host.stage())
            self.assertEqual(host.snapshot().epoch, 1)
            self.assertFalse(old.closed)
        self.assertEqual(host.call("ok"), "tts-1.0.0:lex-1.0.0:ok")
        self.assertEqual(host.live_generations(), (("research:tts@1.0.0", 0, False),))

    def test_close_during_blocked_call_is_nonblocking_and_eventually_cleans(self):
        host = self.host()
        old = host._active.plugin
        original = old.synthesize
        started = threading.Event()
        proceed = threading.Event()
        results = []

        def blocked(text):
            started.set()
            if not proceed.wait(timeout=5):
                raise TimeoutError("caller never resumed")
            return original(text)

        old.synthesize = blocked

        def worker():
            try:
                results.append(host.call("inflight"))
            except BaseException as exc:
                results.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(started.wait(timeout=5))
            host.close()
            self.assertFalse(old.closed)
            with self.assertRaises(RuntimeError):
                host.call("new")
        finally:
            proceed.set()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, ["tts-1.0.0:lex-1.0.0:inflight"])
        self.assertTrue(old.closed)
        self.assertEqual(host.live_generations(), ())

    def test_shutdown_is_idempotent_for_retired_old_generation(self):
        host = self.host()
        old = host._active.plugin
        with host.acquire():
            host.publish_ready(self.upgrade(host))
            self.assertFalse(old.closed)
        self.assertTrue(old.closed)
        host.close()
        host.close()
        self.assertEqual(host.shutdown_errors(), ())
        self.assertEqual(host.live_generations(), ())


if __name__ == "__main__":
    unittest.main()
