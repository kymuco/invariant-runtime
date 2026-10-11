"""Gate A: research-only tests against real Invariant descriptor/resolver objects.

These are not a qualification of in-place PluginService refresh, leases, GPU,
or the separate 14 pre-existing live catalog integration cases.
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from invariant.api import PluginService
from catalog_epoch import CatalogConflict, StalePlan
from gate_a_catalog_view import CatalogViews
from live_service_catalog import LiveServiceCatalog


class CatalogViewGateATests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="invariant-gate-a-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.write_version("1.0.0")
        self.service = PluginService.from_manifest_paths([self.root])
        self.addCleanup(self.service.close)
        self.source = LiveServiceCatalog(self.service, roots=[self.root])
        self.views = CatalogViews(self.source._discover())

    def write_version(self, version: str, *, missing_dependency: bool = False):
        lex = self.root / f"lex-{version}"
        tts = self.root / f"tts-{version}"
        lex.mkdir(exist_ok=True)
        tts.mkdir(exist_ok=True)
        (lex / "plugin.py").write_text(
            "from invariant.spec import BasePlugin\n"
            "class Lexicon(BasePlugin):\n"
            "    def initialize(self, **deps): pass\n"
            "    def shutdown(self): pass\n"
            f"    def convert(self, text): return 'lex-{version}:' + text\n",
            encoding="utf-8",
        )
        (lex / "manifest.json").write_text(json.dumps({
            "manifest_version": 1, "id": "research:lex", "version": version,
            "source": {"code_path": "plugin.py", "class_name": "Lexicon"},
        }), encoding="utf-8")
        (tts / "plugin.py").write_text(
            "from invariant.spec import BasePlugin\n"
            "class TTS(BasePlugin):\n"
            "    def initialize(self, lexicon): self.lexicon = lexicon\n"
            "    def shutdown(self): pass\n"
            f"    def synthesize(self, text): return 'tts-{version}:' + self.lexicon.convert(text)\n",
            encoding="utf-8",
        )
        dependency = "research:lex@9.0.0" if missing_dependency else f"research:lex@{version}"
        (tts / "manifest.json").write_text(json.dumps({
            "manifest_version": 1, "id": "research:tts", "version": version,
            "source": {"code_path": "plugin.py", "class_name": "TTS"},
            "dependencies": [{"id": dependency, "param": "lexicon", "scope": "EXECUTION"}],
        }), encoding="utf-8")

    def add_second(self):
        self.write_version("2.0.0")
        fresh = self.source._discover()
        return self.views.stage(fresh), fresh

    @staticmethod
    def assert_matching_graph(test, view):
        graph = view.resolve_graph("research:tts")
        test.assertIs(graph.view, view)
        test.assertEqual(graph.epoch, view.epoch)
        version = graph.root.rsplit("@", 1)[-1]
        test.assertIn(f"research:tts@{version}", graph.order)
        test.assertIn(f"research:lex@{version}", graph.order)
        test.assertEqual(len(graph.order), 2)
        test.assertLess(graph.order.index(f"research:lex@{version}"),
                        graph.order.index(f"research:tts@{version}"))

    def test_initial_view_resolves_real_pinned_graph(self):
        view = self.views.snapshot()
        self.assertEqual(view.epoch, 1)
        self.assert_matching_graph(self, view)
        self.assertEqual(view.resolve("research:tts").meta.version, "1.0.0")

    def test_publication_is_one_pointer_and_does_not_mutate_plugin_service(self):
        old = self.views.snapshot()
        stage, fresh = self.add_second()
        self.assertIs(self.views.snapshot(), old)
        self.assertFalse(self.service.has_plugin("research:tts@2.0.0"))
        new = self.views.publish(stage, fresh)
        self.assertIs(self.views.snapshot(), new)
        self.assertIsNot(old, new)
        self.assertFalse(self.service.has_plugin("research:tts@2.0.0"))
        self.assert_matching_graph(self, old)
        self.assert_matching_graph(self, new)
        self.assertEqual(old.resolve("research:tts").meta.version, "1.0.0")
        self.assertEqual(new.resolve("research:tts").meta.version, "2.0.0")

    def test_stale_stage_cannot_publish_after_new_epoch(self):
        stage, fresh = self.add_second()
        other = self.views.stage(fresh)
        self.views.publish(stage, fresh)
        with self.assertRaises(StalePlan):
            self.views.publish(other, fresh)
        self.assertEqual(self.views.snapshot().epoch, 2)

    def test_mutated_staged_source_cannot_publish(self):
        stage, _ = self.add_second()
        with (self.root / "tts-2.0.0" / "plugin.py").open("a", encoding="utf-8") as f:
            f.write("\n# changed between staging and publication\n")
        with self.assertRaises(StalePlan):
            self.views.publish(stage, self.source._discover())
        self.assertEqual(self.views.snapshot().epoch, 1)
        self.assertFalse(self.service.has_plugin("research:tts@2.0.0"))

    def test_existing_exact_version_fingerprint_is_immutable(self):
        (self.root / "tts-1.0.0" / "plugin.py").write_text("# modified", encoding="utf-8")
        with self.assertRaises(CatalogConflict):
            self.views.stage(self.source._discover())
        self.assertEqual(self.views.snapshot().epoch, 1)

    def test_invalid_dependency_does_not_publish(self):
        self.write_version("2.0.0", missing_dependency=True)
        with self.assertRaises(Exception):
            self.views.stage(self.source._discover())
        self.assertEqual(self.views.snapshot().epoch, 1)
        self.assert_matching_graph(self, self.views.snapshot())

    def test_retained_view_mapping_is_read_only(self):
        view = self.views.snapshot()
        with self.assertRaises(TypeError):
            view.entries["research:tts@9.0.0"] = None

    def test_concurrent_view_readers_get_whole_dependency_graph(self):
        stage, fresh = self.add_second()
        stop = threading.Event()
        errors = []

        def reader():
            try:
                while not stop.is_set():
                    self.assert_matching_graph(self, self.views.snapshot())
            except BaseException as exc:
                errors.append(exc)
                stop.set()

        threads = [threading.Thread(target=reader) for _ in range(4)]
        for thread in threads:
            thread.start()
        try:
            self.views.publish(stage, fresh)
        finally:
            stop.set()
            for thread in threads:
                thread.join(timeout=5)
        self.assertEqual(errors, [])
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assert_matching_graph(self, self.views.snapshot())

    def test_noop_keeps_same_view_object(self):
        before = self.views.snapshot()
        fresh = self.source._discover()
        stage = self.views.stage(fresh)
        self.assertEqual(stage.added, ())
        self.assertIs(self.views.publish(stage, fresh), before)


if __name__ == "__main__":
    unittest.main()
