# invariant/spec/plugin.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from invariant.spec.context import PluginContext

if TYPE_CHECKING:
    from invariant.exec.runtime.lifecycle import PluginFSM
    from invariant.spec.meta import PluginMeta
else:
    PluginMeta = Any  # avoid runtime import (reduces hard deps for isolated plugins)


class BasePlugin(ABC):
    """
    Базовый класс плагинов.
    """
    fsm: PluginFSM

    def __init__(
        self,
        meta: PluginMeta | None = None,
        context: PluginContext | None = None,
    ):
        self.meta = meta
        self.context = context  # capability-only

    @abstractmethod
    def initialize(self, **deps) -> None:
        raise NotImplementedError

    @abstractmethod
    def shutdown(self) -> None:
        raise NotImplementedError

    def on_event(self, event_name: str, **kwargs: Any) -> None:
        return None
