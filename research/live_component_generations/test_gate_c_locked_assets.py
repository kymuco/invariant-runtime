"""Gate C.4 Windows no-copy model bundles: real Invariant plugin lifecycle.

CPU-only, no CUDA and no model downloads. Windows CreateFileW share-mode is
essential to actual write/delete exclusion, so positive OS tests skip on
other platforms. One cross-platform test proves unsupported OS fails closed.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from catalog_epoch import CatalogConflict, StalePlan
from gate_c_asset_fence import AssetFence
from gate_c_locked_assets import NoCopyModelGuard
from gate_c_managed_call import ManagedCallPort
from gate_b_exclusive_host import RealStopFirstHost


class LockedModelAssetsTests(unittest.TestCase):
    PREFIX = "research.model."

    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="invariant-c4-")
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.claim = self.root / "one-gpu.claim"
        self.paths: dict[str, tuple[Path, ...]] = {}
        self.trusted: dict[str, dict[str, str]] = {}
        self.hosts = []
        self.addCleanup(self.cleanup)
        self.write_version("1.0.0", "v1")

    def cleanup(self):
        for host in reversed(self.hosts):
            try:
                host.close(drain_timeout_s=0, release_timeout_s=0)
            except RuntimeError:
                # Windows test-only fallback: don't leave read-sharing leases
                # behind if a deliberately failed replacement fenced the host.
                inner = getattr(host, "_ManagedCallPort__host", host)
                for bundle in tuple(getattr(inner, "_locked_bundles", {}).values()):
                    bundle.close()
        self.claim.unlink(missing_ok=True)

    def write_version(self, version: str, text: str, *, manifest_config=True):
        folder = self.root / ("model-" + version)
        folder.mkdir(exist_ok=True)
        names = ("part-00001.gguf", "part-00002.gguf", "tokenizer.json")
        files = tuple(folder / name for name in names)
        for index, path in enumerate(files):
            path.write_text(f"{text}/{index}", encoding="utf-8")
        self.paths["research:tts@" + version] = files
        self.trusted["research:tts@" + version] = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files
        }

        source = (
            "from invariant.spec import BasePlugin\n"
            "import json\n"
            "from pathlib import Path\n"
            "class TTS(BasePlugin):\n"
            "    def initialize(self, config):\n"
            f"        self.claim = Path({str(self.claim)!r})\n"
            "        if self.claim.exists(): raise RuntimeError('two GPU owners')\n"
            "        paths = json.loads(config['model_paths_json'])\n"
            "        hashes = json.loads(config['model_sha256_json'])\n"
            f"        if config['root_key'] != 'research:tts@{version}':\n"
            "            raise RuntimeError('wrong model root generation')\n"
            "        if sorted(paths) != sorted(hashes):\n"
            "            raise RuntimeError('incomplete pinned shard inventory')\n"
            "        self.model = '|'.join(\n"
            "            Path(paths[key]).read_text(encoding='utf-8')\n"
            "            for key in sorted(paths)\n"
            "        )\n"
            f"        self.claim.write_text({version!r}, encoding='utf-8')\n"
            "    def shutdown(self):\n"
            "        self.claim.unlink(missing_ok=True)\n"
            f"    def synthesize(self, text): return '{version}:' + self.model + ':' + text\n"
        )
        (folder / "plugin.py").write_text(source, encoding="utf-8")
        manifest = {
            "manifest_version": 1,
            "id": "research:tts", "version": version,
            "source": {"code_path": "plugin.py", "class_name": "TTS"},
        }
        if manifest_config:
            manifest["runtime"] = {
                "config_prefix": self.PREFIX, "config_param": "config",
            }
        (folder / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        return files

    def assets(self, root_key):
        return self.paths[root_key]

    def trusted_sha256(self, root_key):
        return self.trusted[root_key]

    def host(self):
        host = RealStopFirstHost(
            [self.root], root_ref="research:tts",
            is_resource_free=lambda: not self.claim.exists(),
            model_asset_paths=self.assets,
            locked_model_config_prefix=self.PREFIX,
            trusted_model_sha256=self.trusted_sha256,
        )
        self.hosts.append(host)
        return host

    @unittest.skipUnless(sys.platform == "win32", "Windows file shares only")
    def test_three_shards_real_plugin_upgrade_via_managed_port(self):
        port = ManagedCallPort.exclusive(
            [self.root], root_ref="research:tts",
            is_resource_free=lambda: not self.claim.exists(),
            model_asset_paths=self.assets,
            locked_model_config_prefix=self.PREFIX,
            trusted_model_sha256=self.trusted_sha256,
        )
        self.hosts.append(port)
        self.assertEqual(port.invoke("old"), "1.0.0:v1/0|v1/1|v1/2:old")
        self.write_version("2.0.0", "v2")
        self.assertEqual(port.upgrade(), 2)
        self.assertEqual(port.invoke("new"), "2.0.0:v2/0|v2/1|v2/2:new")
        self.assertEqual(self.claim.read_text(), "2.0.0")

    @unittest.skipUnless(sys.platform == "win32", "Windows file shares only")
    def test_active_shard_denies_write_and_delete_until_terminal_close(self):
        host = self.host()
        weight = self.paths["research:tts@1.0.0"][0]
        self.assertEqual(host.call("x"), "1.0.0:v1/0|v1/1|v1/2:x")
        with self.assertRaises(OSError):
            weight.write_text("tamper", encoding="utf-8")
        with self.assertRaises(OSError):
            weight.unlink()
        self.assertEqual(weight.read_text(), "v1/0")
        host.close()
        weight.write_text("after-close", encoding="utf-8")
        self.assertEqual(weight.read_text(), "after-close")

    @unittest.skipUnless(sys.platform == "win32", "Windows file shares only")
    def test_metadata_stage_has_no_disk_copies_and_releases_old_on_upgrade(self):
        host = self.host()
        old_weight = self.paths["research:tts@1.0.0"][0]
        self.write_version("2.0.0", "new")
        stage = host.stage()
        self.assertEqual(len(stage.locked_bundle.handles), 3)
        self.assertEqual(
            {item.path for item in stage.locked_bundle.seal.files},
            set(self.paths["research:tts@2.0.0"]),
        )
        self.assertEqual(len(list(self.root.rglob("*.gguf"))), 4)
        self.assertEqual(host.replace(stage).epoch, 2)
        # Only old generation's OS file handles were retired.
        old_weight.write_text("replaced-v1", encoding="utf-8")
        with self.assertRaises(OSError):
            self.paths["research:tts@2.0.0"][0].unlink()

    @unittest.skipUnless(sys.platform == "win32", "Windows file shares only")
    def test_new_candidate_cannot_be_mutated_between_stage_and_commit(self):
        host = self.host()
        self.write_version("2.0.0", "new")
        stage = host.stage()
        candidate_file = self.paths["research:tts@2.0.0"][1]
        with self.assertRaises(OSError):
            candidate_file.write_bytes(b"changing-while-staged")
        self.assertEqual(host.call("still"), "1.0.0:v1/0|v1/1|v1/2:still")
        self.assertEqual(host.replace(stage).epoch, 2)

    @unittest.skipUnless(sys.platform == "win32", "Windows file shares only")
    def test_preexisting_writer_blocks_stage_before_old_shutdown(self):
        host = self.host()
        self.write_version("2.0.0", "new")
        weight = self.paths["research:tts@2.0.0"][0]
        with weight.open("r+b"):
            with self.assertRaisesRegex(StalePlan, "cannot be locked"):
                host.stage()
        self.assertEqual(host.call("still"), "1.0.0:v1/0|v1/1|v1/2:still")
        self.assertEqual(self.claim.read_text(), "1.0.0")

    @unittest.skipUnless(sys.platform == "win32", "Windows file shares only")
    def test_missing_candidate_config_prevents_old_shutdown(self):
        host = self.host()
        self.write_version("2.0.0", "new", manifest_config=False)
        with self.assertRaisesRegex(CatalogConflict, "explicitly accept"):
            host.stage()
        self.assertEqual(host.call("still"), "1.0.0:v1/0|v1/1|v1/2:still")

    @unittest.skipUnless(sys.platform == "win32", "Windows file shares only")
    def test_missing_shard_or_changed_asset_list_is_rejected(self):
        host = self.host()
        self.write_version("2.0.0", "new")
        self.paths["research:tts@2.0.0"][1].unlink()
        with self.assertRaisesRegex(StalePlan, "unreadable"):
            host.stage()
        self.assertEqual(host.call("still"), "1.0.0:v1/0|v1/1|v1/2:still")

    @unittest.skipUnless(sys.platform == "win32", "Windows file shares only")
    def test_modified_shard_before_stage_rejected_against_trusted_pins(self):
        host = self.host()
        files = self.write_version("2.0.0", "new")
        files[0].write_text("evil", encoding="utf-8")
        with self.assertRaisesRegex(StalePlan, "differ from trusted"):
            host.stage()
        self.assertEqual(host.call("old"), "1.0.0:v1/0|v1/1|v1/2:old")

    @unittest.skipUnless(sys.platform == "win32", "Windows file shares only")
    def test_large_sparse_weight_does_not_trigger_any_snapshot_copy(self):
        host = self.host()
        files = self.write_version("2.0.0", "new")
        with files[0].open("r+b") as stream:
            stream.truncate(20 * 1024 * 1024)  # greater than C.3 16 MiB default
        self.trusted["research:tts@2.0.0"][files[0].name] = (
            hashlib.sha256(files[0].read_bytes()).hexdigest()
        )
        staged = host.stage()
        self.assertEqual(staged.locked_bundle.seal.files[0].size, 20 * 1024 * 1024)
        self.assertFalse(hasattr(host, "_models_copied"))
        self.assertEqual(len(list(self.root.rglob("*.gguf"))), 4)
        self.assertEqual(host.call("still"), "1.0.0:v1/0|v1/1|v1/2:still")

    @unittest.skipUnless(sys.platform == "win32", "Windows file shares only")
    def test_repeated_stage_handles_are_capped(self):
        host = self.host()
        self.write_version("2.0.0", "new")
        for _ in range(4):
            host.stage()
        with self.assertRaisesRegex(StalePlan, "maximum pending"):
            host.stage()
        self.assertEqual(host.call("still"), "1.0.0:v1/0|v1/1|v1/2:still")

    @unittest.skipUnless(sys.platform == "win32", "Windows file shares only")
    def test_uncertain_resource_release_keeps_model_leases_fenced(self):
        from stop_first import Phase, UnsafeRelease

        release_allowed = {"value": True}
        host = RealStopFirstHost(
            [self.root], root_ref="research:tts",
            is_resource_free=(
                lambda: not self.claim.exists() and release_allowed["value"]
            ),
            model_asset_paths=self.assets,
            locked_model_config_prefix=self.PREFIX,
            trusted_model_sha256=self.trusted_sha256,
        )
        self.hosts.append(host)
        self.write_version("2.0.0", "candidate")
        stage = host.stage()
        release_allowed["value"] = False
        with self.assertRaises(UnsafeRelease):
            host.replace(stage, release_timeout_s=0)
        self.assertEqual(host.status().phase, Phase.FAILED)
        self.assertTrue(host._locked_bundles)
        self.assertTrue(all(not b.closed for b in host._locked_bundles.values()))

    @unittest.skipUnless(sys.platform == "win32", "Windows file shares only")
    def test_fail_closed_if_bundle_spans_multiple_directories(self):
        host = self.host()
        files = self.write_version("2.0.0", "new")
        outside = self.root / "foreign.bin"
        outside.write_text("not same directory", encoding="utf-8")
        self.paths["research:tts@2.0.0"] = files + (outside,)
        with self.assertRaisesRegex(StalePlan, "one fixed model directory"):
            host.stage()
        self.assertEqual(host.call("still"), "1.0.0:v1/0|v1/1|v1/2:still")

    def test_no_copy_guard_rejects_unsupported_host_os(self):
        if sys.platform == "win32":
            self.skipTest("Windows covered by native lock tests above")
        seal = AssetFence(self.assets).pin("research:tts@1.0.0")
        guard = NoCopyModelGuard(AssetFence(self.assets), self.trusted_sha256)
        with self.assertRaisesRegex(RuntimeError, "Windows only"):
            guard.acquire(seal)

    def test_mixing_snapshot_copy_and_no_copy_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "cannot combine"):
            RealStopFirstHost(
                [self.root], root_ref="research:tts",
                is_resource_free=lambda: not self.claim.exists(),
                model_asset_paths=self.assets,
                snapshot_config_prefix=self.PREFIX,
                locked_model_config_prefix=self.PREFIX,
            )


if __name__ == "__main__":
    unittest.main()
