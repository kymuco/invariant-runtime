"""Gate C.1: bounded host invocation with actual Invariant plugin fixtures.

CPU-only. Never invokes the native GPU smoke during unittest discovery.
Verifies new opt-in path without modifying or replacing PluginService.get().
"""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from gate_c_managed_call import DeferredOrMutableResult, ManagedCallPort
from prototype import ProviderUnavailable
from stop_first import DrainTimeout, Phase, UnsafeRelease


class ManagedCallPortTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="invariant-c1-port-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.claim = self.root / "exclusive.claim"
        self.write_version("1.0.0")

    def write_version(
        self, version: str, *, exclusive: bool = False,
        stuck: bool = False, bad_result: bool = False,
        bytes_result: bool = False,
    ):
        lex = self.root / ("lex-" + version)
        root = self.root / ("tts-" + version)
        lex.mkdir(exist_ok=True)
        root.mkdir(exist_ok=True)
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
            "    def initialize(self, lexicon):\n"
            "        self.lexicon = lexicon\n"
            "        self.closed = False\n"
        )
        stop = "    def shutdown(self):\n        self.closed = True\n"
        if exclusive:
            init += (
                "        from pathlib import Path\n"
                f"        self.claim = Path({str(self.claim)!r})\n"
                "        if self.claim.exists(): raise RuntimeError('exclusive overlap')\n"
                f"        self.claim.write_text({version!r}, encoding='utf-8')\n"
            )
            if not stuck:
                stop += "        self.claim.unlink(missing_ok=True)\n"
        if bad_result:
            synthesize = "    def synthesize(self, text): return {'plugin': self}\n"
        elif bytes_result:
            synthesize = (
                f"    def synthesize(self, text): return b'{version}:' + text.encode()\n"
            )
        else:
            synthesize = (
                f"    def synthesize(self, text): return 'tts-{version}:' "
                "+ self.lexicon.convert(text)\n"
            )
        (root / "plugin.py").write_text(
            "from invariant.spec import BasePlugin\n"
            "class TTS(BasePlugin):\n" + init + stop + synthesize,
            encoding="utf-8",
        )
        (root / "manifest.json").write_text(json.dumps({
            "manifest_version": 1, "id": "research:tts", "version": version,
            "source": {"code_path": "plugin.py", "class_name": "TTS"},
            "dependencies": [{
                "id": f"research:lex@{version}", "param": "lexicon",
                "scope": "EXECUTION",
            }],
        }), encoding="utf-8")

    def overlap(self):
        port = ManagedCallPort.overlap([self.root], root_ref="research:tts")
        self.addCleanup(port.close)
        return port

    def exclusive(self):
        port = ManagedCallPort.exclusive(
            [self.root], root_ref="research:tts",
            is_resource_free=lambda: not self.claim.exists(),
        )
        def cleanup():
            try:
                port.close(drain_timeout_s=0, release_timeout_s=0)
            except (UnsafeRelease, RuntimeError):
                pass
            self.claim.unlink(missing_ok=True)
        self.addCleanup(cleanup)
        return port

    def test_overlap_port_calls_new_version_and_reclaims_idle_old(self):
        port = self.overlap()
        self.assertEqual(port.invoke("first"), "tts-1.0.0:lex-1.0.0:first")
        inner = port._ManagedCallPort__host  # test-only introspection
        old = inner._active.plugin
        self.write_version("2.0.0")
        self.assertEqual(port.upgrade(), 2)
        self.assertEqual(port.invoke("second"), "tts-2.0.0:lex-2.0.0:second")
        self.assertTrue(old.closed)
        self.assertTrue(old.lexicon.closed)

    def test_overlap_port_keeps_inflight_old_call_alive(self):
        port = self.overlap()
        inner = port._ManagedCallPort__host
        old = inner._active.plugin
        original = old.synthesize
        entered, release = threading.Event(), threading.Event()
        outputs = []

        def blocked(text):
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("old port call was never released")
            return original(text)

        old.synthesize = blocked

        def caller():
            try:
                outputs.append(port.invoke("old"))
            except BaseException as exc:
                outputs.append(exc)

        thread = threading.Thread(target=caller)
        thread.start()
        try:
            self.assertTrue(entered.wait(timeout=5))
            self.write_version("2.0.0")
            self.assertEqual(port.upgrade(), 2)
            self.assertFalse(old.closed)
            self.assertEqual(port.invoke("new"), "tts-2.0.0:lex-2.0.0:new")
        finally:
            release.set()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outputs, ["tts-1.0.0:lex-1.0.0:old"])
        self.assertTrue(old.closed)

    def test_input_and_output_are_strict_detached_values(self):
        self.write_version("1.0.0", bad_result=True)
        port = self.overlap()
        self.assertFalse(hasattr(port, "get"))
        self.assertFalse(hasattr(port, "acquire"))
        self.assertFalse(hasattr(port, "execution"))
        with self.assertRaisesRegex(TypeError, "input must be plain str"):
            port.invoke(42)
        with self.assertRaises(DeferredOrMutableResult):
            port.invoke("no raw plugin returns")

    def test_bytes_result_is_allowed_for_synchronous_audio_payload(self):
        self.write_version("1.0.0", bytes_result=True)
        port = self.overlap()
        self.assertEqual(port.invoke("audio"), b"1.0.0:audio")

    def test_port_close_blocks_new_invocations(self):
        port = self.overlap()
        self.assertEqual(port.invoke("ok"), "tts-1.0.0:lex-1.0.0:ok")
        port.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            port.invoke("denied")
        with self.assertRaisesRegex(RuntimeError, "closed"):
            port.upgrade()

    def test_exclusive_port_manages_real_execution_and_release(self):
        self.write_version("1.0.0", exclusive=True)
        port = self.exclusive()
        self.assertEqual(port.invoke("old"), "tts-1.0.0:lex-1.0.0:old")
        self.assertEqual(self.claim.read_text(encoding="utf-8"), "1.0.0")
        self.write_version("2.0.0", exclusive=True)
        self.assertEqual(port.upgrade(), 2)
        self.assertEqual(self.claim.read_text(encoding="utf-8"), "2.0.0")
        self.assertEqual(port.invoke("new"), "tts-2.0.0:lex-2.0.0:new")
        port.close()
        self.assertFalse(self.claim.exists())

    def test_exclusive_port_drain_timeout_preserves_live_version(self):
        self.write_version("1.0.0", exclusive=True)
        port = self.exclusive()
        host = port._ManagedCallPort__host  # test-only leased hold
        self.write_version("2.0.0", exclusive=True)
        with host._slot.acquire():
            with self.assertRaises(DrainTimeout):
                port.upgrade(drain_timeout_s=0)
            self.assertEqual(self.claim.read_text(encoding="utf-8"), "1.0.0")
        self.assertEqual(port.invoke("still"), "tts-1.0.0:lex-1.0.0:still")

    def test_exclusive_port_fences_when_resource_not_released(self):
        self.write_version("1.0.0", exclusive=True, stuck=True)
        port = self.exclusive()
        self.write_version("2.0.0", exclusive=True)
        with self.assertRaises(UnsafeRelease):
            port.upgrade(release_timeout_s=0)
        with self.assertRaises(ProviderUnavailable):
            port.invoke("never route to unready v2")
        self.assertEqual(self.claim.read_text(encoding="utf-8"), "1.0.0")

    def test_exclusive_port_denies_new_call_during_drain(self):
        self.write_version("1.0.0", exclusive=True)
        port = self.exclusive()
        host = port._ManagedCallPort__host
        old = None
        with host._slot.acquire() as route:
            old = route.plugin
        original = old.synthesize
        started, continue_call = threading.Event(), threading.Event()
        errors, results = [], []

        def blocked(text):
            started.set()
            if not continue_call.wait(timeout=5):
                raise TimeoutError("caller stuck")
            return original(text)

        old.synthesize = blocked

        def call_old():
            try:
                results.append(port.invoke("old"))
            except BaseException as exc:
                errors.append(exc)

        def upgrade():
            try:
                port.upgrade(drain_timeout_s=3)
            except BaseException as exc:
                errors.append(exc)

        self.write_version("2.0.0", exclusive=True)
        caller = threading.Thread(target=call_old)
        caller.start()
        self.assertTrue(started.wait(timeout=5))
        updater = threading.Thread(target=upgrade)
        updater.start()
        try:
            deadline = time.monotonic() + 3
            while host.status().phase is not Phase.DRAINING and time.monotonic() < deadline:
                time.sleep(0.002)
            self.assertEqual(host.status().phase, Phase.DRAINING)
            with self.assertRaises(ProviderUnavailable):
                port.invoke("not admitted")
            self.assertEqual(self.claim.read_text(encoding="utf-8"), "1.0.0")
        finally:
            continue_call.set()
            updater.join(timeout=5)
            caller.join(timeout=5)
        self.assertFalse(updater.is_alive() or caller.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, ["tts-1.0.0:lex-1.0.0:old"])
        self.assertEqual(port.invoke("new"), "tts-2.0.0:lex-2.0.0:new")

    def test_legacy_pluginservice_get_remains_raw_and_unchanged(self):
        from invariant.api import PluginService

        legacy = self.root / "legacy"
        legacy.mkdir()
        (legacy / "plugin.py").write_text(
            "from invariant.spec import BasePlugin\n"
            "class Legacy(BasePlugin):\n"
            "    def initialize(self, **deps): self.closed = False\n"
            "    def shutdown(self): self.closed = True\n"
            "    def synthesize(self, text): return 'legacy:' + text\n",
            encoding="utf-8",
        )
        (legacy / "manifest.json").write_text(json.dumps({
            "manifest_version": 1, "id": "research:legacy", "version": "1.0.0",
            "source": {"code_path": "plugin.py", "class_name": "Legacy"},
        }), encoding="utf-8")
        service = PluginService.from_manifest_paths([legacy])
        self.addCleanup(service.close)
        first = service.get("research:legacy")
        self.assertIs(first, service.get("research:legacy"))
        self.assertEqual(first.synthesize("old API"), "legacy:old API")
        service.shutdown("research:legacy")


if __name__ == "__main__":
    unittest.main()
