"""Gate A.5 readiness-gated publication: real Invariant plugin lifecycle tests.

These use real manifests, loader, ExecutionContext, DependencyResolver,
ActivationTransaction and plugin methods, with one research-only routed root.
The host intentionally RETAINS old generations until quiesced close; it has
no Gate B leases, no VRAM coordination and no production public API.
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from catalog_epoch import CatalogConflict, StalePlan
from gate_a_readiness_host import ReadinessGatedHost


class ReadinessPublicationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="invariant-ready-route-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.write_version("1.0.0")

    def write_version(
        self, version: str, *, fail: bool = False, no_method: bool = False
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
            "    def initialize(self, lexicon): raise RuntimeError('candidate startup failed')\n"
            if fail else
            "    def initialize(self, lexicon): self.lexicon = lexicon; self.closed = False\n"
        )
        method = (
            "" if no_method else
            f"    def synthesize(self, text): return 'tts-{version}:' + self.lexicon.convert(text)\n"
        )
        (tts / "plugin.py").write_text(
            "from invariant.spec import BasePlugin\n"
            "class TTS(BasePlugin):\n"
            + init
            + "    def shutdown(self): self.closed = True\n"
            + method,
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

    def host(self) -> ReadinessGatedHost:
        host = ReadinessGatedHost([self.root], root_ref="research:tts")
        self.addCleanup(host.close)
        return host

    def test_failed_candidate_never_advances_live_route(self):
        host = self.host()
        current = host.snapshot()
        self.write_version("2.0.0", fail=True)
        with self.assertRaisesRegex(RuntimeError, "candidate startup failed"):
            host.prepare(host.stage())
        self.assertIs(host.snapshot(), current)
        self.assertEqual(host.call("still"), "tts-1.0.0:lex-1.0.0:still")
        self.assertEqual(host.snapshot().epoch, 1)

    def test_prepared_ready_instance_is_same_instance_after_publish(self):
        host = self.host()
        old = host._active.plugin  # research-only observable identity
        self.write_version("2.0.0")
        staged = host.stage()
        prepared = host.prepare(staged)
        self.assertEqual(prepared.status, "prepared")
        self.assertEqual(host.snapshot().epoch, 1)
        self.assertEqual(host.call("a"), "tts-1.0.0:lex-1.0.0:a")
        self.assertFalse(old.closed)
        after = host.publish_ready(prepared)
        self.assertEqual(prepared.status, "published")
        self.assertIs(host._active.plugin, prepared.plugin)
        self.assertIs(host.snapshot(), after)
        self.assertEqual(after.epoch, 2)
        self.assertEqual(host.call("b"), "tts-2.0.0:lex-2.0.0:b")
        self.assertFalse(old.closed)
        self.assertFalse(prepared.plugin.closed)
        host.close()
        self.assertTrue(old.closed)
        self.assertTrue(prepared.plugin.closed)

    def test_missing_required_method_cannot_publish(self):
        host = self.host()
        self.write_version("2.0.0", no_method=True)
        with self.assertRaisesRegex(CatalogConflict, "required method"):
            host.prepare(host.stage())
        self.assertEqual(host.snapshot().epoch, 1)
        self.assertEqual(host.call("ok"), "tts-1.0.0:lex-1.0.0:ok")

    def test_mutation_after_ready_preflight_discards_candidate(self):
        host = self.host()
        self.write_version("2.0.0")
        candidate = host.prepare(host.stage())
        with (self.root / "tts-2.0.0" / "plugin.py").open("a", encoding="utf-8") as f:
            f.write("\n# changed after candidate was initialized\n")
        with self.assertRaises(StalePlan):
            host.publish_ready(candidate)
        self.assertEqual(candidate.status, "discarded")
        self.assertTrue(candidate.plugin.closed)
        self.assertEqual(host.snapshot().epoch, 1)
        self.assertEqual(host.call("ok"), "tts-1.0.0:lex-1.0.0:ok")

    def test_stale_prepared_contender_is_closed_and_cannot_publish(self):
        host = self.host()
        self.write_version("2.0.0")
        first, second = host.stage(), host.stage()
        winner = host.prepare(first)
        stale = host.prepare(second)
        host.publish_ready(winner)
        with self.assertRaises(StalePlan):
            host.publish_ready(stale)
        self.assertEqual(stale.status, "discarded")
        self.assertTrue(stale.plugin.closed)
        self.assertEqual(host.snapshot().epoch, 2)
        self.assertEqual(host.call("ok"), "tts-2.0.0:lex-2.0.0:ok")

    def test_preparation_blocks_without_rerouting_old_calls(self):
        host = self.host()
        self.write_version("2.0.0")
        stage = host.stage()
        reached = threading.Event()
        resume = threading.Event()
        errors = []
        prepared = []
        original = host._ensure_method

        def wait_after_ready(plugin):
            original(plugin)
            if plugin.meta.version == "2.0.0":
                reached.set()
                if not resume.wait(timeout=5):
                    raise TimeoutError("prepare was not released")

        host._ensure_method = wait_after_ready

        def worker():
            try:
                prepared.append(host.prepare(stage))
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(reached.wait(timeout=5))
            self.assertEqual(host.snapshot().epoch, 1)
            self.assertEqual(host.call("old"), "tts-1.0.0:lex-1.0.0:old")
        finally:
            resume.set()
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(prepared), 1)
        self.assertEqual(host.snapshot().epoch, 1)
        host.publish_ready(prepared[0])
        self.assertEqual(host.call("new"), "tts-2.0.0:lex-2.0.0:new")

    def test_inflight_old_call_finishes_after_route_switch(self):
        host = self.host()
        old = host._active.plugin
        real = old.synthesize
        entered = threading.Event()
        resume = threading.Event()
        result = []

        def blocked(text):
            entered.set()
            if not resume.wait(timeout=5):
                raise TimeoutError("old call was not released")
            return real(text)

        old.synthesize = blocked

        def worker():
            try:
                result.append(host.call("before"))
            except BaseException as exc:
                result.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(timeout=5))
            self.write_version("2.0.0")
            host.publish_ready(host.prepare(host.stage()))
            self.assertEqual(host.call("after"), "tts-2.0.0:lex-2.0.0:after")
            self.assertFalse(old.closed)
        finally:
            resume.set()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, ["tts-1.0.0:lex-1.0.0:before"])

    def test_discard_and_closed_host_fail_closed(self):
        host = self.host()
        self.write_version("2.0.0")
        stage = host.stage()
        candidate = host.prepare(stage)
        host.discard(candidate)
        self.assertEqual(candidate.status, "discarded")
        self.assertTrue(candidate.plugin.closed)
        with self.assertRaises(CatalogConflict):
            host.publish_ready(candidate)
        self.assertEqual(host.snapshot().epoch, 1)
        host.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            host.call("blocked")
        with self.assertRaisesRegex(RuntimeError, "closed"):
            host.stage()


if __name__ == "__main__":
    unittest.main()
