"""Gate C.3: real Invariant plugin consumes the pre-copied verified asset.

CPU-only integration tests. This is an opt-in tiny-file experiment: no
large weight copies, real CUDA, sandboxing or external immutable CAS.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from catalog_epoch import CatalogConflict, StalePlan
from gate_c_managed_call import ManagedCallPort
from gate_b_exclusive_host import RealStopFirstHost
from stop_first import Phase


class ModelSnapshotConsumptionTests(unittest.TestCase):
    PREFIX = "research.asset."

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="invariant-c3-e2e-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.claim = self.root / "exclusive.claim"
        self.model_files: dict[str, list[Path]] = {}
        self.hosts = []
        self.addCleanup(self.cleanup)
        self.write_version("1.0.0", "original-v1")

    def cleanup(self):
        for host in self.hosts:
            try:
                host.close(drain_timeout_s=0, release_timeout_s=0)
            except RuntimeError:
                pass
        self.claim.unlink(missing_ok=True)

    def write_version(
        self, version: str, content: str, *, mutate_source=False,
        config_contract=True,
    ):
        folder = self.root / ("tts-" + version)
        folder.mkdir(exist_ok=True)
        weights = folder / "weights.bin"
        weights.write_text(content, encoding="utf-8")
        self.model_files[f"research:tts@{version}"] = [weights]
        init = (
            "    def initialize(self, config):\n"
            "        from pathlib import Path\n"
            f"        original = Path({str(weights)!r})\n"
            f"        claim = Path({str(self.claim)!r})\n"
            "        if claim.exists(): raise RuntimeError('exclusive overlap')\n"
            "        path = Path(config['model_path'])\n"
            "        if path == original: raise RuntimeError('not a private snapshot')\n"
            "        self.loaded_snapshot = str(path)\n"
        )
        if mutate_source:
            init += "        original.write_text('modified-after-verify', encoding='utf-8')\n"
        init += (
            "        self.model = path.read_text(encoding='utf-8')\n"
            f"        claim.write_text({version!r}, encoding='utf-8')\n"
            "        self.claim = claim\n"
        )
        (folder / "plugin.py").write_text(
            "from invariant.spec import BasePlugin\n"
            "class TTS(BasePlugin):\n"
            + init
            + "    def shutdown(self): self.claim.unlink(missing_ok=True)\n"
            + f"    def synthesize(self, text): return '{version}:' + self.model + ':' + text\n",
            encoding="utf-8",
        )
        manifest = {
            "manifest_version": 1, "id": "research:tts", "version": version,
            "source": {"code_path": "plugin.py", "class_name": "TTS"},
        }
        if config_contract:
            manifest["runtime"] = {
                "config_prefix": self.PREFIX, "config_param": "config",
            }
        (folder / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        return weights

    def assets(self, root_key):
        return tuple(self.model_files[root_key])

    def host(self, *, limit=1024 * 1024):
        host = RealStopFirstHost(
            [self.root], root_ref="research:tts",
            is_resource_free=lambda: not self.claim.exists(),
            model_asset_paths=self.assets,
            snapshot_config_prefix=self.PREFIX,
            snapshot_max_file_bytes=limit,
        )
        self.hosts.append(host)
        return host

    def test_managed_port_uses_verified_snapshot_after_upgrade(self):
        port = ManagedCallPort.exclusive(
            [self.root], root_ref="research:tts",
            is_resource_free=lambda: not self.claim.exists(),
            model_asset_paths=self.assets,
            snapshot_config_prefix=self.PREFIX,
        )
        self.hosts.append(port)
        self.assertEqual(port.invoke("old"), "1.0.0:original-v1:old")
        self.write_version("2.0.0", "original-v2")
        self.assertEqual(port.upgrade(), 2)
        self.assertEqual(port.invoke("new"), "2.0.0:original-v2:new")
        self.assertEqual(self.claim.read_text(), "2.0.0")

    def test_plugin_modifies_original_at_load_but_consumes_snapshot_bytes(self):
        host = self.host()
        self.assertEqual(host.call("old"), "1.0.0:original-v1:old")
        original = self.write_version(
            "2.0.0", "expected-v2", mutate_source=True
        )
        staged = host.stage()
        self.assertEqual(original.read_text(), "expected-v2")
        self.assertIsNotNone(staged.asset_snapshot)
        snapshot_path = staged.asset_snapshot.file
        self.assertNotEqual(original, snapshot_path)
        self.assertEqual(snapshot_path.read_text(), "expected-v2")
        self.assertEqual(host.replace(staged).epoch, 2)
        # The real initialized plugin has written to the UNTRUSTED source
        # before reading the host-injected snapshot through config.
        self.assertEqual(original.read_text(), "modified-after-verify")
        self.assertEqual(host.call("answer"), "2.0.0:expected-v2:answer")
        self.assertTrue(snapshot_path.exists())

    def test_snapshot_tampering_before_replace_preserves_live_v1(self):
        host = self.host()
        self.write_version("2.0.0", "expected-v2")
        stage = host.stage()
        snapshot = stage.asset_snapshot.file
        snapshot.chmod(0o666)
        snapshot.write_text("altered-v2", encoding="utf-8")
        with self.assertRaisesRegex(StalePlan, "snapshot altered"):
            host.replace(stage)
        self.assertEqual(host.status().phase, Phase.ACTIVE)
        self.assertEqual(host.call("safe"), "1.0.0:original-v1:safe")
        self.assertEqual(self.claim.read_text(), "1.0.0")

    def test_missing_explicit_manifest_config_rejected_before_old_stop(self):
        host = self.host()
        self.write_version("2.0.0", "candidate", config_contract=False)
        with self.assertRaisesRegex(CatalogConflict, "explicitly accept"):
            host.stage()
        self.assertEqual(host.call("safe"), "1.0.0:original-v1:safe")
        self.assertEqual(self.claim.read_text(), "1.0.0")

    def test_snapshot_budget_rejected_before_old_stop(self):
        host = self.host(limit=1024)
        self.write_version("2.0.0", "X" * 1100)
        with self.assertRaisesRegex(StalePlan, "explicit limit"):
            host.stage()
        self.assertEqual(host.call("safe"), "1.0.0:original-v1:safe")

    def test_model_source_modified_after_stage_rejected_before_drain(self):
        host = self.host()
        weights = self.write_version("2.0.0", "expected-v2")
        stage = host.stage()
        weights.write_text("changed-before-replace", encoding="utf-8")
        with self.assertRaisesRegex(StalePlan, "model assets changed"):
            host.replace(stage)
        self.assertEqual(host.call("safe"), "1.0.0:original-v1:safe")

    def test_multishard_not_silently_treated_as_complete_snapshot(self):
        host = self.host()
        weights = self.write_version("2.0.0", "shard-1")
        other = weights.parent / "shard-2"
        other.write_text("shard-2", encoding="utf-8")
        self.model_files["research:tts@2.0.0"].append(other)
        with self.assertRaisesRegex(StalePlan, "one model artifact"):
            host.stage()
        self.assertEqual(host.call("safe"), "1.0.0:original-v1:safe")

    def test_repeated_stage_copies_have_a_small_total_budget(self):
        host = self.host()
        self.write_version("2.0.0", "candidate")
        # Initial v1 snapshot consumes one of four available slots.
        for _ in range(3):
            host.stage()
        with self.assertRaisesRegex(StalePlan, "snapshot budget exhausted"):
            host.stage()
        self.assertEqual(host.call("safe"), "1.0.0:original-v1:safe")

    def test_terminal_close_deletes_private_model_copy(self):
        host = self.host()
        with host._slot.acquire() as active:
            filename = Path(active.plugin.loaded_snapshot)
        self.assertTrue(filename.is_file())
        host.close()
        self.assertFalse(filename.exists())
        self.assertFalse(self.claim.exists())


if __name__ == "__main__":
    unittest.main()
