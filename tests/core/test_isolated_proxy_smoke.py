from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from invariant.load.isolated_proxy import IsolatedPluginProxy
from invariant.spec.descriptor import PluginDescriptor
from invariant.spec.source import PluginSourceSpec


@dataclass
class DummyMeta:
    id: str
    version: str | None = None


class FakeEnvInfo:
    def __init__(self, python_exe: Path) -> None:
        self.python_exe = python_exe


class FakeEnvManager:
    # IMPORTANT: no venv, no pip, no lock — pure proxy/worker test
    def ensure_env(self, *, plugin_key: str, plugin_root: Path, deps) -> FakeEnvInfo:
        return FakeEnvInfo(Path(sys.executable))


def _make_descriptor(tmp_path: Path) -> PluginDescriptor:
    plugin_root = tmp_path / "plug"
    plugin_root.mkdir(parents=True, exist_ok=True)

    # minimal plugin file
    (plugin_root / "plugin.py").write_text(
        """
import time
import sys

class TestPlugin:
    def initialize(self, **deps):
        self._deps = dict(deps)

    def echo(self, x):
        return x

    def noisy(self):
        print("NOISE_TO_STDOUT")
        sys.stdout.flush()
        return 123

    def sleepy(self, seconds: float):
        time.sleep(float(seconds))
        return "done"

    def shutdown(self):
        return None
""".strip(),
        encoding="utf-8",
    )

    meta = DummyMeta(id="test:isolated", version="0.1.0")

    source = PluginSourceSpec(
        kind="manifest",
        module="test_plugin_mod",
        class_name="TestPlugin",
        path=(plugin_root / "plugin.py"),
        extra={
            "plugin_root": str(plugin_root),
            "code_rel": "plugin.py",
            # force isolated path, but FakeEnvManager делает это “без pip”
            "deps": {"mode": "online", "requirements": "requirements.txt"},
            "call_timeout_s": 2.0,
            "method_timeouts": {"sleepy": 0.2},
        },
    )

    return PluginDescriptor(meta=meta, source=source)


def test_isolated_proxy_basic_roundtrip(tmp_path: Path):
    d = _make_descriptor(tmp_path)
    p = IsolatedPluginProxy(descriptor=d, env_manager=FakeEnvManager())

    try:
        assert p.echo("hi") == "hi"
        # plugin prints to stdout — should NOT corrupt JSON protocol
        assert p.noisy() == 123
    finally:
        p.close()


def test_isolated_proxy_timeout_closes_worker(tmp_path: Path):
    d = _make_descriptor(tmp_path)
    p = IsolatedPluginProxy(descriptor=d, env_manager=FakeEnvManager())

    try:
        t0 = time.monotonic()
        with pytest.raises(RuntimeError):
            # explicit timeout override
            p.call("sleepy", 2, timeout_s=0.2)
        assert (time.monotonic() - t0) < 2.0

        # after timeout the worker is considered tainted and closed
        with pytest.raises(Exception):  # noqa: B017
            p.echo("still there?")
    finally:
        p.close()
