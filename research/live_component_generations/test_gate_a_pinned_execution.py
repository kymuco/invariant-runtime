"""Gate A: positive real ExecutionContext activation pinned to a catalog view.

All fixtures use real manifest discovery, PluginRegistry, DependencyResolver,
ExecutionRuntime, ActivationTransaction, and plugin Python classes. The
research bridge itself is NOT production/API code.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from invariant.api import PluginService

from catalog_epoch import CatalogConflict
from gate_a_catalog_view import CatalogViews
from gate_a_pinned_execution import capture_catalog_view, open_pinned_execution
from live_service_catalog import LiveServiceCatalog


class PinnedActivationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="invariant-pinned-activation-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def write_lex(self, version: str) -> None:
        directory = self.root / f"lex-{version}"
        directory.mkdir(exist_ok=True)
        (directory / "plugin.py").write_text(
            "from invariant.spec import BasePlugin\n"
            "class Lexicon(BasePlugin):\n"
            f"    def initialize(self, **deps): self.tag = {version!r}\n"
            "    def shutdown(self): pass\n"
            "    def convert(self, text): return 'lex-' + self.tag + ':' + text\n",
            encoding="utf-8",
        )
        (directory / "manifest.json").write_text(json.dumps({
            "manifest_version": 1, "id": "research:lex", "version": version,
            "source": {"code_path": "plugin.py", "class_name": "Lexicon"},
        }), encoding="utf-8")

    def write_tts(
        self,
        version: str,
        *,
        dependency: str,
        scope: str = "EXECUTION",
        fail_init: bool = False,
    ) -> None:
        directory = self.root / f"tts-{version}"
        directory.mkdir(exist_ok=True)
        initialize = (
            "    def initialize(self, lexicon): raise RuntimeError('candidate startup failed')\n"
            if fail_init else
            "    def initialize(self, lexicon): self.lexicon = lexicon\n"
        )
        (directory / "plugin.py").write_text(
            "from invariant.spec import BasePlugin\n"
            "class TTS(BasePlugin):\n"
            + initialize
            + "    def shutdown(self): pass\n"
            + f"    def synthesize(self, text): return 'tts-{version}:' + self.lexicon.convert(text)\n",
            encoding="utf-8",
        )
        (directory / "manifest.json").write_text(json.dumps({
            "manifest_version": 1, "id": "research:tts", "version": version,
            "source": {"code_path": "plugin.py", "class_name": "TTS"},
            "dependencies": [{
                "id": dependency, "param": "lexicon", "scope": scope
            }],
        }), encoding="utf-8")

    def open_service(self):
        service = PluginService.from_manifest_paths([self.root])
        self.addCleanup(service.close)
        return service

    def test_same_mutation_point_yields_pinned_injected_dependency(self):
        """Corresponds to the green NEGATIVE characterization in prior commit."""
        self.write_lex("1.0.0")
        self.write_tts("1.0.0", dependency="research:lex")
        service = self.open_service()
        old_view = capture_catalog_view(service)
        self.assertEqual(
            old_view.resolve_graph("research:tts").order,
            ("research:lex@1.0.0", "research:tts@1.0.0"),
        )
        context = open_pinned_execution(service, old_view)
        self.addCleanup(context.close)

        runtime = context._root_runtime  # research-only characterization hook
        collect = runtime.resolver.collect
        evidence = {"mutated": False}

        def collect_then_mutate(ref):
            collect(ref)
            if evidence["mutated"] or ref != "research:tts@1.0.0":
                return
            self.write_lex("2.0.0")
            new_lex = next(
                d for d in service.loader.discover(
                    mode="strict", fail_fast=True, include_unstable=False
                )
                if d.meta.id == "research:lex" and d.meta.version == "2.0.0"
            )
            service.registry.register(new_lex)
            evidence["mutated"] = True

        runtime.resolver.collect = collect_then_mutate
        old_instance = context.get("research:tts@1.0.0")

        self.assertTrue(evidence["mutated"])
        self.assertEqual(service.registry.resolve("research:lex").meta.version, "2.0.0")
        self.assertEqual(old_view.resolve("research:lex").meta.version, "1.0.0")
        self.assertEqual(old_instance.lexicon.tag, "1.0.0")
        self.assertEqual(old_instance.synthesize("probe"), "tts-1.0.0:lex-1.0.0:probe")

        # A separately captured view can now deliberately choose the new
        # latest dependency; it never silently changes the old context.
        newer_view = capture_catalog_view(service, epoch=2)
        new_context = open_pinned_execution(service, newer_view)
        self.addCleanup(new_context.close)
        self.assertEqual(
            new_context.get("research:tts@1.0.0").lexicon.tag, "2.0.0"
        )
        self.assertEqual(old_instance.lexicon.tag, "1.0.0")

    def test_catalog_cas_switch_does_not_redirect_old_context(self):
        self.write_lex("1.0.0")
        self.write_tts("1.0.0", dependency="research:lex@1.0.0")
        service = self.open_service()
        source = LiveServiceCatalog(service, roots=[self.root])
        views = CatalogViews(source._discover())
        old = views.snapshot()
        old_context = open_pinned_execution(service, old)
        self.addCleanup(old_context.close)

        # New descriptor inventory is staged, not committed to the live
        # PluginService registry. The view pointer is the only opt-in read path.
        self.write_lex("2.0.0")
        self.write_tts("2.0.0", dependency="research:lex@2.0.0")
        candidates = source._discover()
        plan = views.stage(candidates)
        new = views.publish(plan, candidates)
        self.assertFalse(service.has_plugin("research:tts@2.0.0"))
        self.assertEqual(old_context.get("research:tts").synthesize("x"),
                         "tts-1.0.0:lex-1.0.0:x")

        new_context = open_pinned_execution(service, new)
        self.addCleanup(new_context.close)
        self.assertEqual(new_context.get("research:tts").synthesize("x"),
                         "tts-2.0.0:lex-2.0.0:x")
        self.assertEqual(old_context.get("research:tts").synthesize("y"),
                         "tts-1.0.0:lex-1.0.0:y")

    def test_failed_candidate_activation_keeps_old_execution(self):
        self.write_lex("1.0.0")
        self.write_tts("1.0.0", dependency="research:lex@1.0.0")
        service = self.open_service()
        source = LiveServiceCatalog(service, roots=[self.root])
        views = CatalogViews(source._discover())
        old_context = open_pinned_execution(service, views.snapshot())
        self.addCleanup(old_context.close)
        old_instance = old_context.get("research:tts")

        self.write_lex("2.0.0")
        self.write_tts("2.0.0", dependency="research:lex@2.0.0", fail_init=True)
        candidates = source._discover()
        views.publish(views.stage(candidates), candidates)
        candidate_context = open_pinned_execution(service, views.snapshot())
        self.addCleanup(candidate_context.close)
        with self.assertRaisesRegex(RuntimeError, "candidate startup failed"):
            candidate_context.get("research:tts")
        self.assertEqual(old_instance.synthesize("still-running"),
                         "tts-1.0.0:lex-1.0.0:still-running")

    def test_mixed_scope_fails_closed_in_capture(self):
        self.write_lex("1.0.0")
        self.write_tts("1.0.0", dependency="research:lex@1.0.0", scope="SINGLETON")
        service = self.open_service()
        with self.assertRaisesRegex(CatalogConflict, "mandatory EXECUTION"):
            capture_catalog_view(service)


if __name__ == "__main__":
    unittest.main()
