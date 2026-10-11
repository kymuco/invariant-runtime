"""Gate A.4: one authoritative catalog pointer, real execution contexts.

This tests a research-owned host rather than the production PluginService.
Passing proves no competing mutable registry is required for the scoped
EXECUTION-only activation path; it does not prove production API compatibility.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from catalog_epoch import CatalogConflict, StalePlan
from gate_a_catalog_owned_host import CatalogOwnedHost


class SingleCatalogOwnerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="invariant-single-catalog-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def write_lex(self, version: str):
        directory = self.root / f"lex-{version}"
        directory.mkdir(exist_ok=True)
        (directory / "plugin.py").write_text(
            "from invariant.spec import BasePlugin\n"
            "class Lexicon(BasePlugin):\n"
            f"    def initialize(self, **deps): self.version = {version!r}\n"
            "    def shutdown(self): pass\n"
            "    def convert(self, text): return 'lex-' + self.version + ':' + text\n",
            encoding="utf-8",
        )
        (directory / "manifest.json").write_text(json.dumps({
            "manifest_version": 1,
            "id": "research:lex",
            "version": version,
            "source": {"code_path": "plugin.py", "class_name": "Lexicon"},
        }), encoding="utf-8")

    def write_tts(self, version: str, *, dep: str, scope="EXECUTION", fail_init=False):
        directory = self.root / f"tts-{version}"
        directory.mkdir(exist_ok=True)
        initialize = (
            "    def initialize(self, lexicon): raise RuntimeError('candidate rejected')\n"
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
            "manifest_version": 1,
            "id": "research:tts",
            "version": version,
            "source": {"code_path": "plugin.py", "class_name": "TTS"},
            "dependencies": [{"id": dep, "param": "lexicon", "scope": scope}],
        }), encoding="utf-8")

    def initial(self, *, unpinned=False, scope="EXECUTION"):
        self.write_lex("1.0.0")
        self.write_tts("1.0.0", dep="research:lex" if unpinned else
                       "research:lex@1.0.0", scope=scope)
        return CatalogOwnedHost([self.root])

    def add_v2(self, *, fail_init=False):
        self.write_lex("2.0.0")
        self.write_tts("2.0.0", dep="research:lex@2.0.0", fail_init=fail_init)

    def test_one_authoritative_snapshot_routes_old_and_new_executions(self):
        host = self.initial()
        self.assertFalse(hasattr(host, "registry"))
        before = host.snapshot()
        with host.execution() as previous:
            old_instance = previous.get("research:tts")
            self.assertEqual(old_instance.synthesize("one"),
                             "tts-1.0.0:lex-1.0.0:one")
            self.add_v2()
            stage = host.stage()
            new_view = host.publish(stage)
            self.assertIsNot(new_view, before)
            self.assertEqual(new_view.epoch, 2)
            with host.execution() as new_context:
                self.assertEqual(new_context.get("research:tts").synthesize("two"),
                                 "tts-2.0.0:lex-2.0.0:two")
            self.assertEqual(previous.get("research:tts").synthesize("three"),
                             "tts-1.0.0:lex-1.0.0:three")
        self.assertEqual(before.resolve_graph("research:tts").root,
                         "research:tts@1.0.0")

    def test_runtime_inspector_uses_retained_epoch_inventory(self):
        from invariant.exec.caps.ids import CAP_INSPECTOR

        host = self.initial()
        with host.execution() as old:
            old_inspector = old.capabilities.get(CAP_INSPECTOR)
            initial = {
                (entry["id"], entry["version"])
                for entry in old_inspector.list_descriptors()
            }
            self.assertEqual(initial, {
                ("research:lex", "1.0.0"),
                ("research:tts", "1.0.0"),
            })

            self.add_v2()
            host.publish(host.stage())
            old_inventory_after = {
                (entry["id"], entry["version"])
                for entry in old_inspector.list_descriptors()
            }
            self.assertEqual(initial, old_inventory_after)
            with host.execution() as current:
                new_inspector = current.capabilities.get(CAP_INSPECTOR)
                new_inventory = {
                    (entry["id"], entry["version"])
                    for entry in new_inspector.list_descriptors()
                }
                self.assertIn(("research:tts", "2.0.0"), new_inventory)
                self.assertIn(("research:lex", "2.0.0"), new_inventory)

    def test_stale_compare_and_swap_does_not_alter_catalog(self):
        host = self.initial()
        self.add_v2()
        first = host.stage()
        second = host.stage()
        accepted = host.publish(first)
        with self.assertRaises(StalePlan):
            host.publish(second)
        self.assertIs(host.snapshot(), accepted)

    def test_source_change_after_stage_is_rejected(self):
        host = self.initial()
        self.add_v2()
        candidate = host.stage()
        with (self.root / "tts-2.0.0" / "plugin.py").open("a", encoding="utf-8") as file:
            file.write("\n# artifact mutated after staging\n")
        with self.assertRaises(StalePlan):
            host.publish(candidate)
        self.assertEqual(host.snapshot().epoch, 1)

    def test_unpinned_old_manifest_rebind_fails_closed(self):
        host = self.initial(unpinned=True)
        baseline = host.snapshot()
        self.assertEqual(baseline.resolve_graph("research:tts").order,
                         ("research:lex@1.0.0", "research:tts@1.0.0"))
        self.write_lex("2.0.0")
        with self.assertRaisesRegex(CatalogConflict, "mutation"):
            host.stage()
        self.assertIs(host.snapshot(), baseline)

    def test_unsupported_singleton_scope_rejected_at_discovery(self):
        with self.assertRaisesRegex(CatalogConflict, "scope"):
            self.initial(scope="SINGLETON")

    def test_failed_candidate_does_not_shutdown_running_old_context(self):
        host = self.initial()
        with host.execution() as old:
            previous = old.get("research:tts")
            self.add_v2(fail_init=True)
            host.publish(host.stage())
            with host.execution() as candidate:
                with self.assertRaisesRegex(RuntimeError, "candidate rejected"):
                    candidate.get("research:tts")
            self.assertEqual(previous.synthesize("alive"),
                             "tts-1.0.0:lex-1.0.0:alive")

    def test_duplicate_identity_is_not_silently_deduped(self):
        host = self.initial()
        duplicate = self.root / "duplicate"
        duplicate.mkdir()
        original = self.root / "tts-1.0.0"
        (duplicate / "manifest.json").write_bytes((original / "manifest.json").read_bytes())
        (duplicate / "plugin.py").write_bytes((original / "plugin.py").read_bytes())
        with self.assertRaisesRegex(CatalogConflict, "duplicate"):
            host.stage()
        self.assertEqual(host.snapshot().epoch, 1)


if __name__ == "__main__":
    unittest.main()
