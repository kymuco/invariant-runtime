from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from invariant.load.env.env_manager import EnvInfo
from invariant.load.isolated_proxy import IsolatedPluginProxy
from invariant.spec.descriptor import PluginDescriptor
from invariant.spec.errors import PluginLoadError
from invariant.spec.source import PluginSourceSpec


@dataclass(frozen=True)
class FakeMeta:
    id: str
    version: str | None = "0.0.0+test"
    name: str | None = "Тест"
    description: str | None = None
    author: str | None = None
    repo: str | None = None
    maturity: str | None = "stable"


class FakeEnvManager:
    """
    Не создаём venv и не ставим deps — просто запускаем воркер
    тем же python, но В ОТДЕЛЬНОМ ПРОЦЕССЕ.
    """

    def ensure_env(self, *, plugin_key: str, plugin_root: Path, deps: Any) -> EnvInfo:  # noqa: ARG002
        return EnvInfo(env_dir=plugin_root, python_exe=Path(sys.executable), key="fake-env")


def _write_plugin(root: Path, rel: str, code: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    p = (root / rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(code, encoding="utf-8")
    return p


def _make_descriptor(*, plugin_root: Path, code_rel: str) -> PluginDescriptor:
    code_path = (plugin_root / code_rel).resolve()

    deps_raw = {
        "mode": "online",              # важно: чтобы IsolatedPluginProxy согласился
        "requirements": "requirements.txt",
    }

    source = PluginSourceSpec(
        kind="manifest",
        module="invariant_test_isolated_mod",
        class_name="IsoPlugin",
        path=code_path,
        extra={
            "plugin_root": str(plugin_root),
            "code_rel": code_rel,
            "deps": deps_raw,
        },
    )

    meta = FakeMeta(id="test:isolated")
    return PluginDescriptor(meta=meta, source=source)


@pytest.fixture()
def plugin_root(tmp_path: Path) -> Path:
    return tmp_path / "plugroot"


def test_isolated_proxy_basic_roundtrip(plugin_root: Path) -> None:
    # requirements.txt (FakeEnvManager его не читает, но PluginDepsSpec требует поле)
    _write_plugin(plugin_root, "requirements.txt", "")

    _write_plugin(
        plugin_root,
        "plugin.py",
        (
            "print('NOISE: import print should NOT break protocol')\n"
            "class IsoPlugin:\n"
            "    def __init__(self, meta=None, context=None):\n"
            "        self.meta = meta\n"
            "        self._deps = {}\n"
            "    def initialize(self, **deps):\n"
            "        print('NOISE: initialize print')\n"
            "        self._deps = dict(deps)\n"
            "    def get_init(self):\n"
            "        return self._deps\n"
            "    def get_meta_id(self):\n"
            "        m = self.meta\n"
            "        if isinstance(m, dict):\n"
            "            return m.get('id')\n"
            "        return getattr(m, 'id', None)\n"
            "    def echo(self, s):\n"
            "        print('NOISE: echo print', s)\n"
            "        return s\n"
            "    def shutdown(self):\n"
            "        print('NOISE: shutdown print')\n"
        ),
    )

    d = _make_descriptor(plugin_root=plugin_root, code_rel="plugin.py")
    proxy = IsolatedPluginProxy(descriptor=d, env_manager=FakeEnvManager())
    try:
        proxy.initialize(foo=123, bar="ok")
        assert proxy.get_init() == {"foo": 123, "bar": "ok"}

        # Unicode roundtrip (важно для Windows, если encoding сломан)
        out = proxy.echo("привет")
        assert out == "привет"

        assert proxy.get_meta_id() == "test:isolated"
    finally:
        proxy.close()

    # best-effort: процесс должен быть убит
    p = proxy._client._proc  # noqa: SLF001 (тестовый доступ)
    for _ in range(20):
        if p.poll() is not None:
            break
        time.sleep(0.05)
    assert p.poll() is not None


def test_isolated_proxy_exception_contains_traceback(plugin_root: Path) -> None:
    _write_plugin(plugin_root, "requirements.txt", "")

    _write_plugin(
        plugin_root,
        "plugin.py",
        (
            "class IsoPlugin:\n"
            "    def explode(self):\n"
            "        raise RuntimeError('boom')\n"
        ),
    )

    d = _make_descriptor(plugin_root=plugin_root, code_rel="plugin.py")
    proxy = IsolatedPluginProxy(descriptor=d, env_manager=FakeEnvManager())
    try:
        with pytest.raises(RuntimeError) as e:
            proxy.explode()
        msg = str(e.value)
        assert "boom" in msg
        # worker обычно присылает traceback, прокси добавляет его к сообщению
        assert "traceback" in msg.lower() or "Traceback" in msg
    finally:
        proxy.close()


def test_isolated_proxy_blocks_path_escape(plugin_root: Path, tmp_path: Path) -> None:
    _write_plugin(plugin_root, "requirements.txt", "")

    outside = tmp_path / "outside.py"
    outside.write_text("class IsoPlugin: pass\n", encoding="utf-8")

    # code_rel выходит за пределы plugin_root
    d = _make_descriptor(plugin_root=plugin_root, code_rel="../outside.py")

    with pytest.raises(PluginLoadError):
        _ = IsolatedPluginProxy(descriptor=d, env_manager=FakeEnvManager())
