from __future__ import annotations

import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from invariant.load.env.env_manager import EnvInfo
from invariant.load.isolated_proxy import IsolatedPluginProxy
from invariant.spec.descriptor import PluginDescriptor
from invariant.spec.source import PluginSourceSpec


@dataclass(frozen=True, slots=True)
class MiniMeta:
    # Нам реально нужны только id/version (остальное optional).
    id: str
    version: str | None = "0.1.0"
    name: str | None = None
    description: str | None = None
    author: str | None = None
    repo: str | None = None
    maturity: str = "stable"
    priority: int = 0


class FakeEnvManager:
    """
    Unit-test EnvManager:
    - НИКАКИХ venv/pip
    - Возвращаем текущий python, чтобы воркер стартовал мгновенно
    """
    def ensure_env(self, *, plugin_key: str, plugin_root: Path, deps) -> EnvInfo:  # noqa: ANN001
        return EnvInfo(env_dir=plugin_root, python_exe=Path(sys.executable), key=f"fake:{plugin_key}")


def _make_descriptor(tmp_path: Path) -> PluginDescriptor:
    plugin_root = tmp_path / "plug"
    plugin_root.mkdir(parents=True, exist_ok=True)

    code = textwrap.dedent(
        """
        import time

        class SimplePlugin:
            def __init__(self, meta=None, context=None):
                self.meta = meta
                self.context = context

            def initialize(self, **deps):
                # специально печатаем — воркер должен не сломать JSON протокол
                print("initialize called", deps)
                return None

            def shutdown(self):
                print("shutdown called")
                return None

            def ping(self):
                return "pong"

            def add(self, a, b):
                return a + b

            def slow(self, t):
                time.sleep(float(t))
                return "done"
        """
    ).strip()

    (plugin_root / "plugin.py").write_text(code, encoding="utf-8")
    (plugin_root / "req.txt").write_text("", encoding="utf-8")  # keep contract consistent

    # deps должен быть "isolated" по твоей логике, но env мы не создаём (FakeEnvManager)
    deps = {
        "mode": "online",
        "requirements": "req.txt",  # не используется FakeEnvManager'ом
        "index_url": None,
        "extra_index_urls": [],
        "trusted_hosts": [],
        "only_binary": True,
    }

    src = PluginSourceSpec(
        kind="manifest",
        path=(plugin_root / "plugin.py"),
        module=None,
        class_name="SimplePlugin",
        extra={
            "plugin_root": str(plugin_root),
            "code_rel": "plugin.py",
            "deps": deps,
            # ускоряем тесты/фейлы (чтобы не висели)
            "call_timeout_s": 1.0,
            "method_timeouts": {"slow": 0.2},
        },
    )

    return PluginDescriptor(
        meta=MiniMeta(id="test:isolated_proxy_unit", version="0.1.0"),  # type: ignore[arg-type]
        source=src,
    )


def test_isolated_proxy_unit_roundtrip(tmp_path: Path, caplog) -> None:
    d = _make_descriptor(tmp_path)
    env = FakeEnvManager()

    proxy: IsolatedPluginProxy | None = None
    try:
        proxy = IsolatedPluginProxy(descriptor=d, env_manager=env)  # type: ignore[arg-type]

        # Пингуем через явный call(), чтобы задать таймауты
        assert proxy.call("ping", timeout_s=1.0) == "pong"
        assert proxy.call("add", 2, 3, timeout_s=1.0) == 5

        # initialize печатает в stdout — протокол должен пережить
        # а вывод должен уйти в stderr воркера и быть видимым на стороне хоста
        caplog.set_level("INFO")
        proxy.initialize()  # без deps
        assert any("initialize called" in r.message for r in caplog.records), \
            "worker stderr output is not surfaced (check stderr->logger piping in _WorkerClient)"

        # method_timeouts-проверка: timeout_s не передаём, должен примениться method_timeouts["slow"]=0.2
        t0 = time.monotonic()
        with pytest.raises(RuntimeError):
            proxy.call("slow", 2.0)
        assert (time.monotonic() - t0) < 1.0, "method timeout did not trigger quickly"

        # явный override всё ещё должен работать
        with pytest.raises(RuntimeError):
            proxy.call("slow", 2.0, timeout_s=0.1)

    finally:
        if proxy is not None:
            proxy.close()

def test_isolated_proxy_rejects_non_json_deps(tmp_path: Path) -> None:
    d = _make_descriptor(tmp_path)
    env = FakeEnvManager()
    proxy = IsolatedPluginProxy(descriptor=d, env_manager=env)  # type: ignore[arg-type]
    try:
        with pytest.raises(RuntimeError):
            proxy.initialize(bad=Path("x"))  # Path is not JSON-serializable
    finally:
        proxy.close()