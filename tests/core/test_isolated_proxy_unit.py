from __future__ import annotations

import logging
import sys
import textwrap
import threading
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


class _WorkerStderrReceipt(logging.Handler):
    """Signal only after pytest's earlier root capture handler got the line."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.seen = threading.Event()

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if (
            record.name == "invariant.isolated.worker"
            and message.startswith("[worker:")
            and "initialize called" in message
        ):
            self.seen.set()


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

        # The RPC response and worker stderr use DIFFERENT pipes and
        # independent parent reader threads. A successful initialize()
        # response does not guarantee stderr has reached caplog yet.
        caplog.set_level(logging.INFO, logger="invariant.isolated.worker")
        receipt = _WorkerStderrReceipt()
        root_logger = logging.getLogger()
        # caplog's handler is already installed on root; append our
        # receipt AFTER it so the Event means caplog has seen the line.
        root_logger.addHandler(receipt)
        try:
            proxy.initialize()  # no deps
            assert receipt.seen.wait(timeout=3.0), (
                "worker stderr output did not reach the host logger"
            )
            assert any(
                record.name == "invariant.isolated.worker"
                and record.getMessage().startswith("[worker:")
                and "initialize called" in record.getMessage()
                for record in caplog.records
            ), "worker stderr output was not captured by pytest"
        finally:
            root_logger.removeHandler(receipt)
            receipt.close()

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