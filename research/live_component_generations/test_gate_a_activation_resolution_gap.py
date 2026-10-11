"""Characterization: real activation may re-resolve an unpinned dependency.

This test must remain research-only. A PASS is *negative evidence*:
the activation graph and the actual injected dependency disagree after a
deterministically timed registry mutation. It is NOT a hot-refresh feature.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from invariant.api import EphemeralPolicy, PluginService


class ActivationResolutionGapTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="invariant-activation-gap-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.write_lex("1.0.0")
        self.write_tts()
        self.service = PluginService.from_manifest_paths([self.root])
        self.addCleanup(self.service.close)

    def write_lex(self, version: str) -> None:
        location = self.root / ("lex-" + version)
        location.mkdir(exist_ok=True)
        (location / "plugin.py").write_text(
            "from invariant.spec import BasePlugin\n"
            "class Lexicon(BasePlugin):\n"
            f"    def initialize(self, **deps): self.tag = {version!r}\n"
            "    def shutdown(self): pass\n"
            "    def convert(self, text): return 'lex-' + self.tag + ':' + text\n",
            encoding="utf-8",
        )
        (location / "manifest.json").write_text(json.dumps({
            "manifest_version": 1, "id": "research:lex", "version": version,
            "source": {"code_path": "plugin.py", "class_name": "Lexicon"},
        }), encoding="utf-8")

    def write_tts(self) -> None:
        location = self.root / "tts-1.0.0"
        location.mkdir(exist_ok=True)
        (location / "plugin.py").write_text(
            "from invariant.spec import BasePlugin\n"
            "class TTS(BasePlugin):\n"
            "    def initialize(self, lexicon): self.lexicon = lexicon\n"
            "    def shutdown(self): pass\n"
            "    def synthesize(self, text): return 'tts:' + self.lexicon.convert(text)\n",
            encoding="utf-8",
        )
        (location / "manifest.json").write_text(json.dumps({
            "manifest_version": 1, "id": "research:tts", "version": "1.0.0",
            "source": {"code_path": "plugin.py", "class_name": "TTS"},
            "dependencies": [{
                # Deliberately unpinned: permitted by current production resolver.
                "id": "research:lex", "param": "lexicon", "scope": "EXECUTION",
            }],
        }), encoding="utf-8")

    def test_resolver_graph_and_initialized_dependency_can_diverge(self):
        context = self.service.execution(policy=EphemeralPolicy())
        self.addCleanup(context.close)
        runtime = context._root_runtime  # Research characterization only.
        resolver = runtime.resolver
        original_collect = resolver.collect
        evidence = {"registered": False}

        def collect_then_publish(plugin_ref):
            original_collect(plugin_ref)
            if plugin_ref != "research:tts@1.0.0" or evidence["registered"]:
                return

            # The root's dependency graph was collected from v1.
            evidence["graph_before"] = tuple(resolver.execution_order())
            self.write_lex("2.0.0")
            new_version = next(
                d for d in self.service.loader.discover(
                    mode="strict", fail_fast=True, include_unstable=False
                )
                if d.meta.id == "research:lex" and d.meta.version == "2.0.0"
            )
            # Mutable registry writes are an old API behavior being probed.
            # This is not the proposed catalog publication mechanism.
            self.service.registry.register(new_version)
            evidence["registered"] = True

        resolver.collect = collect_then_publish
        instance = context.get("research:tts@1.0.0")

        self.assertTrue(evidence["registered"])
        self.assertIn("research:lex@1.0.0", evidence["graph_before"])
        self.assertNotIn("research:lex@2.0.0", evidence["graph_before"])
        self.assertEqual(instance.lexicon.tag, "2.0.0")
        self.assertEqual(instance.synthesize("probe"), "tts:lex-2.0.0:probe")


if __name__ == "__main__":
    unittest.main()
