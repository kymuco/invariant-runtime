"""Against REAL invariant.api only; run from a repository checkout.

No mocks of PluginService, registry, resolver or lifecycle are used. The tests
only create temporary manifest artifacts and use public-facing APIs.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from prototype import ProviderSlot
from public_facade_bridge import CatalogGeneration, register_generation


class PublicFacadeBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = tempfile.TemporaryDirectory(prefix="invariant-live-catalog-")
        self.addCleanup(self.work.cleanup)
        self.slot = ProviderSlot("speech.synthesize@1")
        self.addCleanup(self.slot.deactivate)
        self.root = Path(self.work.name)

    def make_catalog(
        self,
        label: str,
        *,
        version: str,
        lex_version: str | None = None,
        requires: str | None = None,
        bad_manifest: bool = False,
        no_method: bool = False,
        fail_initialize: bool = False,
        cycle: bool = False,
    ) -> CatalogGeneration:
        root = self.root / label
        lex_version = lex_version or version
        lex_path = root / "lexicon"
        tts_path = root / "tts"
        lex_path.mkdir(parents=True)
        tts_path.mkdir(parents=True)
        lex_code = (
            "from invariant.spec import BasePlugin\n\n"
            "class LexiconPlugin(BasePlugin):\n"
            "    def initialize(self, **deps):\n"
            "        self.closed = False\n"
            "    def shutdown(self):\n"
            "        self.closed = True\n"
            "    def convert(self, text):\n"
            f"        return 'lexicon-{lex_version}:' + text\n"
        )
        (lex_path / "plugin.py").write_text(lex_code, encoding="utf-8")
        lex_manifest = {
            "manifest_version": 1,
            "id": "research:lexicon",
            "version": lex_version,
            "source": {"code_path": "plugin.py", "class_name": "LexiconPlugin"},
        }
        if cycle:
            lex_manifest["dependencies"] = [
                {"id": f"research:tts@{version}", "param": "tts", "scope": "EXECUTION"}
            ]
        (lex_path / "manifest.json").write_text(json.dumps(lex_manifest), encoding="utf-8")

        tts_code = (
            "from invariant.spec import BasePlugin\n\n"
            "class TTSPlugin(BasePlugin):\n"
            "    def initialize(self, lexicon):\n"
            + ("        raise RuntimeError('candidate initialization failed')\n" if fail_initialize else
               "        self.lexicon = lexicon\n        self.closed = False\n")
            + "    def shutdown(self):\n        self.closed = True\n"
            + ("" if no_method else
               "    def synthesize(self, text):\n"
               f"        return 'tts-{version}:' + self.lexicon.convert(text)\n")
        )
        (tts_path / "plugin.py").write_text(tts_code, encoding="utf-8")
        tts_manifest = {
            "manifest_version": 2 if bad_manifest else 1,
            "id": "research:tts",
            "version": version,
            "source": {"code_path": "plugin.py", "class_name": "TTSPlugin"},
            "dependencies": [
                {"id": requires or f"research:lexicon@{lex_version}",
                 "param": "lexicon", "scope": "EXECUTION"}
            ],
        }
        (tts_path / "manifest.json").write_text(json.dumps(tts_manifest), encoding="utf-8")
        return CatalogGeneration("speech.synthesize@1", "research:tts", version, (root,))

    def stage(self, label: str, *, version: str, **kwargs) -> None:
        register_generation(self.slot, self.make_catalog(label, version=version, **kwargs))

    def test_install_recipe_does_not_activate_real_plugin(self) -> None:
        self.stage("v1", version="1.0.0")
        self.assertEqual(self.slot.installed(), (("research:tts", "1.0.0"),))
        self.assertEqual(self.slot.active(), None)
        self.assertEqual(self.slot.shutdown_errors(), ())

    def test_real_manifest_loader_and_pinned_dependency(self) -> None:
        self.stage("v1", version="1.0.0", lex_version="1.0.0")
        self.slot.switch("research:tts", "1.0.0")
        with self.slot.acquire() as bound:
            self.assertEqual(bound.provider.synthesize("hello"),
                             "tts-1.0.0:lexicon-1.0.0:hello")
            self.assertEqual(bound.provider.meta.version, "1.0.0")
            self.assertEqual(bound.provider.lexicon.meta.version, "1.0.0")
            self.assertFalse(bound.closed)

    def test_new_catalog_generation_does_not_mutate_old_service(self) -> None:
        self.stage("v1", version="1.0.0")
        self.slot.switch("research:tts", "1.0.0")
        with self.slot.acquire() as old:
            self.assertFalse(old.service.has_plugin("research:tts@2.0.0"))
            self.stage("v2", version="2.0.0")
            self.assertFalse(old.service.has_plugin("research:tts@2.0.0"))
            self.slot.switch("research:tts", "2.0.0")
            self.assertEqual(old.provider.synthesize("old"),
                             "tts-1.0.0:lexicon-1.0.0:old")
            self.assertFalse(old.provider.closed)
            with self.slot.acquire() as new:
                self.assertEqual(new.provider.synthesize("new"),
                                 "tts-2.0.0:lexicon-2.0.0:new")
                self.assertIsNot(new.service, old.service)
                self.assertTrue(new.service.has_plugin("research:tts@2.0.0"))
        self.assertTrue(old.closed)
        self.assertTrue(old.provider.closed)
        self.assertTrue(old.provider.lexicon.closed)
        self.assertEqual(old.provider.fsm.state.name, "SHUTDOWN")
        self.assertEqual(old.provider.lexicon.fsm.state.name, "SHUTDOWN")
        self.assertEqual(self.slot.shutdown_errors(), ())

    def test_missing_exact_dependency_rejects_candidate_without_disruption(self) -> None:
        self.stage("v1", version="1.0.0")
        self.slot.switch("research:tts", "1.0.0")
        self.stage("v2", version="2.0.0", requires="research:lexicon@9.0.0")
        with self.assertRaises(Exception):
            self.slot.switch("research:tts", "2.0.0")
        self.assertEqual(self.slot.active(), ("research:tts", "1.0.0"))
        with self.slot.acquire() as old:
            self.assertEqual(old.provider.synthesize("still"),
                             "tts-1.0.0:lexicon-1.0.0:still")
        self.assertEqual(self.slot.shutdown_errors(), ())

    def test_dependency_cycle_rejects_candidate_without_disruption(self) -> None:
        self.stage("v1", version="1.0.0")
        self.slot.switch("research:tts", "1.0.0")
        self.stage("v2", version="2.0.0", cycle=True)
        with self.assertRaises(Exception):
            self.slot.switch("research:tts", "2.0.0")
        self.assertEqual(self.slot.active(), ("research:tts", "1.0.0"))
        self.assertEqual(self.slot.shutdown_errors(), ())

    def test_bad_manifest_is_not_published_as_running_generation(self) -> None:
        self.stage("v1", version="1.0.0")
        self.slot.switch("research:tts", "1.0.0")
        self.stage("bad", version="2.0.0", bad_manifest=True)
        with self.assertRaises(Exception):
            self.slot.switch("research:tts", "2.0.0")
        self.assertEqual(self.slot.active(), ("research:tts", "1.0.0"))

    def test_host_required_method_missing_rejects_candidate(self) -> None:
        self.stage("v1", version="1.0.0")
        self.slot.switch("research:tts", "1.0.0")
        self.stage("bad", version="2.0.0", no_method=True)
        with self.assertRaisesRegex(TypeError, "host-required callable"):
            self.slot.switch("research:tts", "2.0.0")
        self.assertEqual(self.slot.active(), ("research:tts", "1.0.0"))

    def test_initialize_failure_does_not_change_active_binding(self) -> None:
        self.stage("v1", version="1.0.0")
        self.slot.switch("research:tts", "1.0.0")
        self.stage("bad", version="2.0.0", fail_initialize=True)
        with self.assertRaises(Exception):
            self.slot.switch("research:tts", "2.0.0")
        with self.slot.acquire() as old:
            self.assertFalse(old.closed)
            self.assertEqual(old.provider.synthesize("hello"),
                             "tts-1.0.0:lexicon-1.0.0:hello")

    def test_shutdown_only_after_last_lease_is_released(self) -> None:
        self.stage("v1", version="1.0.0")
        self.stage("v2", version="2.0.0")
        self.slot.switch("research:tts", "1.0.0")
        with self.slot.acquire() as old:
            self.slot.switch("research:tts", "2.0.0")
            self.assertFalse(old.closed)
            self.assertFalse(old.provider.lexicon.closed)
        self.assertTrue(old.closed)
        self.assertTrue(old.provider.lexicon.closed)

    def test_host_contract_mismatch_does_not_install(self) -> None:
        candidate = self.make_catalog("v1", version="1.0.0")
        other = CatalogGeneration("vision.ocr@1", candidate.plugin_id,
                                  candidate.version, candidate.paths)
        with self.assertRaisesRegex(ValueError, "functional contract mismatch"):
            register_generation(self.slot, other)
        self.assertEqual(self.slot.installed(), ())


if __name__ == "__main__":
    unittest.main()
