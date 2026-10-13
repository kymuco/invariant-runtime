"""Gate C.2 research: explicit model-weight asset identity across stop-first.

Tests are CPU-only and use real manifest-backed Invariant ExecutionContexts.
They never launch CUDA or require model weights; synthetic small files represent
model bytes, not true LLM checkpoint/load behavior.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from catalog_epoch import StalePlan
from gate_c_asset_fence import AssetFence
from gate_c_managed_call import ManagedCallPort
from gate_b_exclusive_host import RealStopFirstHost
from stop_first import CandidateFailed, Phase


class ModelAssetIdentityTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="invariant-model-assets-")
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.claim = self.root / "exclusive.claim"
        self.weights: dict[str, Path] = {}
        self.declarations: dict[str, list[Path]] = {}
        self.write_version("1.0.0", "weights-v1")
        self.hosts = []
        self.addCleanup(self.close_hosts)

    def close_hosts(self):
        for host in self.hosts:
            try:
                host.close(drain_timeout_s=0, release_timeout_s=0)
            except RuntimeError:
                pass
        self.claim.unlink(missing_ok=True)

    def write_version(self, version: str, data: str):
        folder = self.root / ("tts-" + version)
        folder.mkdir(exist_ok=True)
        weight = folder / "model.safetensors"
        weight.write_bytes(data.encode("utf-8"))
        self.weights[version] = weight
        root_key = "research:tts@" + version
        self.declarations[root_key] = [weight]
        (folder / "plugin.py").write_text(
            "from invariant.spec import BasePlugin\n"
            "from pathlib import Path\n"
            "class TTS(BasePlugin):\n"
            "    def initialize(self, **deps):\n"
            f"        self.weight = Path({str(weight)!r})\n"
            f"        self.claim = Path({str(self.claim)!r})\n"
            "        if self.claim.exists(): raise RuntimeError('exclusive overlap')\n"
            "        self.model = self.weight.read_text(encoding='utf-8')\n"
            f"        self.claim.write_text({version!r}, encoding='utf-8')\n"
            "        self.closed = False\n"
            "    def shutdown(self):\n"
            "        self.closed = True\n"
            "        self.claim.unlink(missing_ok=True)\n"
            f"    def synthesize(self, text): return '{version}:' + self.model + ':' + text\n",
            encoding="utf-8",
        )
        (folder / "manifest.json").write_text(
            json.dumps({
                "manifest_version": 1,
                "id": "research:tts",
                "version": version,
                "source": {"code_path": "plugin.py", "class_name": "TTS"},
            }),
            encoding="utf-8",
        )

    def paths_for(self, root_key: str):
        return tuple(self.declarations[root_key])

    def host(self, *, probe=None):
        h = RealStopFirstHost(
            [self.root], root_ref="research:tts",
            is_resource_free=probe or (lambda: not self.claim.exists()),
            model_asset_paths=self.paths_for,
        )
        self.hosts.append(h)
        return h

    def test_unchanged_weight_snapshot_allows_real_plugin_upgrade(self):
        port = ManagedCallPort.exclusive(
            [self.root], root_ref="research:tts",
            is_resource_free=lambda: not self.claim.exists(),
            model_asset_paths=self.paths_for,
        )
        self.hosts.append(port)
        self.assertEqual(port.invoke("old"), "1.0.0:weights-v1:old")
        self.write_version("2.0.0", "weights-v2")
        self.assertEqual(port.upgrade(), 2)
        self.assertEqual(port.invoke("new"), "2.0.0:weights-v2:new")
        self.assertEqual(self.claim.read_text(encoding="utf-8"), "2.0.0")

    def test_changed_weight_before_replace_rejected_without_old_stop(self):
        host = self.host()
        old = host._slot._active[1].plugin
        self.write_version("2.0.0", "candidate-original")
        stage = host.stage()
        self.weights["2.0.0"].write_bytes(b"candidate-mutated")
        with self.assertRaisesRegex(StalePlan, "model assets changed"):
            host.replace(stage)
        self.assertIs(host._slot._active[1].plugin, old)
        self.assertFalse(old.closed)
        self.assertEqual(host.snapshot().epoch, 1)
        self.assertEqual(host.call("still"), "1.0.0:weights-v1:still")
        self.assertEqual(self.claim.read_text(encoding="utf-8"), "1.0.0")

    def test_missing_weight_rejected_before_exclusive_drain(self):
        host = self.host()
        self.write_version("2.0.0", "candidate-original")
        stage = host.stage()
        self.weights["2.0.0"].unlink()
        with self.assertRaisesRegex(StalePlan, "model asset unreadable"):
            host.replace(stage)
        self.assertEqual(host.snapshot().epoch, 1)
        self.assertEqual(host.call("still"), "1.0.0:weights-v1:still")

    def test_changed_declared_file_list_is_stale(self):
        host = self.host()
        self.write_version("2.0.0", "weights-v2")
        stage = host.stage()
        companion = self.weights["2.0.0"].parent / "tokenizer.json"
        companion.write_text('{"new":true}', encoding="utf-8")
        self.declarations["research:tts@2.0.0"].append(companion)
        with self.assertRaisesRegex(StalePlan, "model assets changed"):
            host.replace(stage)
        self.assertEqual(host.call("old"), "1.0.0:weights-v1:old")

    def test_staged_asset_altered_after_resource_release_restores_old(self):
        flag = {"mutate": False}

        def release_probe():
            is_free = not self.claim.exists()
            if is_free and flag["mutate"]:
                flag["mutate"] = False
                self.weights["2.0.0"].write_bytes(b"late-mutation")
            return is_free

        host = self.host(probe=release_probe)
        self.write_version("2.0.0", "weights-v2")
        stage = host.stage()
        flag["mutate"] = True
        with self.assertRaises(CandidateFailed) as caught:
            host.replace(stage, release_timeout_s=1)
        self.assertTrue(caught.exception.restored)
        self.assertEqual(host.status().phase, Phase.ACTIVE)
        self.assertEqual(host.snapshot().epoch, 1)
        self.assertEqual(host.call("restored"), "1.0.0:weights-v1:restored")
        self.assertEqual(self.claim.read_text(encoding="utf-8"), "1.0.0")

    def test_symlink_weight_cannot_be_pinned(self):
        self.write_version("2.0.0", "weights-v2")
        target = self.weights["2.0.0"]
        linked = target.parent / "linked.bin"
        try:
            linked.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlink privilege unavailable on this OS")
        self.declarations["research:tts@2.0.0"] = [linked]
        fence = AssetFence(self.paths_for)
        with self.assertRaisesRegex(StalePlan, "symbolic-link"):
            fence.pin("research:tts@2.0.0")

    def test_duplicate_or_undeclared_assets_fail_closed(self):
        fence = AssetFence(self.paths_for)
        self.declarations["research:tts@1.0.0"] = []
        with self.assertRaisesRegex(StalePlan, "declare 1..128"):
            fence.pin("research:tts@1.0.0")
        self.declarations["research:tts@1.0.0"] = [
            self.weights["1.0.0"], self.weights["1.0.0"]
        ]
        with self.assertRaisesRegex(StalePlan, "duplicate"):
            fence.pin("research:tts@1.0.0")

    def test_model_hash_detects_same_size_change_even_if_mtime_reset(self):
        fence = AssetFence(self.paths_for)
        pin = fence.pin("research:tts@1.0.0")
        weight = self.weights["1.0.0"]
        original_stat = weight.stat()
        weight.write_bytes(b"weights-x1")
        # Explicit file-byte digest matters more than time stamps.
        self.assertEqual(weight.stat().st_size, original_stat.st_size)
        import os

        os.utime(weight, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        with self.assertRaisesRegex(StalePlan, "model assets changed"):
            fence.verify(pin)


if __name__ == "__main__":
    unittest.main()
