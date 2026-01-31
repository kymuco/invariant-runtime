# invariant/spec/plugin.py
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Optional

from invariant.spec.meta import PluginMeta
from invariant.spec.context import PluginContext


class BasePlugin(ABC):
    """
    Базовый класс плагинов.
    НЕ управляет своим жизненным циклом.
    """
    def __init__(
        self,
        meta: PluginMeta | None = None,
        context: Optional[PluginContext] = None,
    ):
        self.meta = meta
        self.context = context  # capability-only

    @abstractmethod
    def initialize(self, **deps) -> None:
        """
        Вызывается PluginService.
        Плагин не меняет состояние.
        """
        pass

    @abstractmethod
    def shutdown(self) -> None:
        """
        Cleanup.
        Плагин не меняет состояние.
        """
        pass

    def on_event(self, event_name: str, **kwargs: Any) -> None:
        pass