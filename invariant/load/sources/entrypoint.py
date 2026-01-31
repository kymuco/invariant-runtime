from __future__ import annotations

import logging
from typing import List, Optional, Type, Any

from invariant.load.sources.base import PluginSource
from invariant.spec.descriptor import PluginDescriptor
from invariant.spec.source import PluginSourceSpec
from invariant.spec.plugin import BasePlugin
from invariant.spec.errors import PluginLoadError

logger = logging.getLogger(__name__)


class EntrypointSource(PluginSource):
    """
    Entrypoint-based source.

    Stub implementation:
    - discover() returns empty list
    - load() raises PluginLoadError

    When implemented, this should load plugins from Python entry points
    (e.g. importlib.metadata.entry_points()).
    """

    kind = "entrypoint"

    def __init__(self, group: str = "invariant.plugins"):
        self.group = group

    def discover(
        self,
        base_class: Optional[Type] = None,
        include_unstable: bool = False,
        *,
        dev_mode: bool = False,
        fail_fast: bool = False,
        **kwargs: Any,
    ) -> List[PluginDescriptor]:
        logger.debug("[EntrypointSource] discover() stub: group=%s", self.group)
        return []

    def can_load(self, source: PluginSourceSpec) -> bool:
        return source.kind == self.kind

    def load(
        self,
        source: PluginSourceSpec,
        registry=None,
        *,
        descriptor: PluginDescriptor | None = None,
        **kwargs: Any,
    ) -> BasePlugin:
        raise PluginLoadError(
            "EntrypointSource is not implemented yet",
            meta_id=getattr(getattr(descriptor, "meta", None), "id", None),
        )
