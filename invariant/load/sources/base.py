from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from invariant.spec.descriptor import PluginDescriptor
from invariant.spec.plugin import BasePlugin
from invariant.spec.source import PluginSourceSpec


class PluginSource(ABC):
    """
    Strategy for discovering and loading plugins.
    """

    kind: str  # every source should define it

    @abstractmethod
    def discover(
        self,
        base_class: type | None = None,
        include_unstable: bool = False,
        *,
        dev_mode: bool = False,
        fail_fast: bool = False,
        **kwargs: Any,
    ) -> list[PluginDescriptor]:
        """
        Discover and return PluginDescriptor list.

        dev_mode:
        - True: allow versionless plugins (registry will assign 0.0.0+local.*)
        - False: versionless plugins should raise PluginLoadError

        fail_fast:
        - True: any error should propagate
        - False: source may log and skip invalid candidates
        """
        raise NotImplementedError

    @abstractmethod
    def can_load(self, source: PluginSourceSpec) -> bool:
        """
        Определяет, может ли источник загрузить этот плагин.
        """
        raise NotImplementedError

    @abstractmethod
    def load(
        self,
        source: PluginSourceSpec,
        registry=None,
        *,
        descriptor: PluginDescriptor | None = None,
        **kwargs: Any,
    ) -> BasePlugin:
        """
        Загрузить и вернуть экземпляр плагина.
        """
        raise NotImplementedError
