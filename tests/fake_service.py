from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


class _FakeFSM:
    def __init__(self, plugin_id: str):
        self.plugin_id = plugin_id


class EchoPlugin:
    def __init__(self, plugin_id: str):
        self.fsm = _FakeFSM(plugin_id)

    def echo(self, x: Any) -> Any:
        return x


class SleepPlugin:
    def __init__(self, plugin_id: str):
        self.fsm = _FakeFSM(plugin_id)

    def sleep(self, seconds: float = 0.05) -> str:
        time.sleep(float(seconds))
        return "ok"


class FailPlugin:
    def __init__(self, plugin_id: str):
        self.fsm = _FakeFSM(plugin_id)

    def boom(self) -> None:
        raise RuntimeError("boom")


@dataclass
class FakeDescriptorMeta:
    id: str
    version: str = "0.0.0"
    name: str | None = None
    dependencies: list = None


@dataclass
class FakeDescriptor:
    meta: FakeDescriptorMeta


class FakeRegistry:
    def __init__(self) -> None:
        self._items = [
            FakeDescriptor(FakeDescriptorMeta(id="tests:echo", name="Echo")),
            FakeDescriptor(FakeDescriptorMeta(id="tests:sleep", name="Sleep")),
            FakeDescriptor(FakeDescriptorMeta(id="tests:fail", name="Fail")),
        ]

    def list(self):
        return list(self._items)


class FakeExecutionContext:
    """
    Minimal ExecutionContext surface that your ExecutionSupervisor uses:
      - get(plugin_id)
      - close()
    """
    def __init__(self) -> None:
        self._closed = False
        self._lock = threading.RLock()
        self._plugins: dict[str, Any] = {}

    def get(self, plugin_id: str) -> Any:
        with self._lock:
            if self._closed:
                raise RuntimeError("ExecutionContext is closed")

            p = self._plugins.get(plugin_id)
            if p is not None:
                return p

            if plugin_id == "tests:echo":
                p = EchoPlugin(plugin_id)
            elif plugin_id == "tests:sleep":
                p = SleepPlugin(plugin_id)
            elif plugin_id == "tests:fail":
                p = FailPlugin(plugin_id)
            else:
                raise KeyError(f"Unknown plugin_id: {plugin_id}")

            self._plugins[plugin_id] = p
            return p

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._plugins.clear()


class FakePluginService:
    """
    Minimal PluginService surface used by RuntimeHost/ExecutionSupervisor:
      - execution(policy=..., dependency_overrides=...)
      - registry.list()
      - tracer (optional)
      - close() (optional)
    """
    def __init__(self, *, tracer=None) -> None:
        self.tracer = tracer
        self.registry = FakeRegistry()

    def execution(self, *, policy, dependency_overrides=None):
        # policy/overrides are intentionally accepted but not used here
        return FakeExecutionContext()

    def close(self) -> None:
        # nothing to close for fake
        return
