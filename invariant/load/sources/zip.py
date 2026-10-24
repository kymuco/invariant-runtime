from __future__ import annotations

import logging
from typing import Any

from invariant.load.sources.base import PluginSource
from invariant.spec.descriptor import PluginDescriptor
from invariant.spec.errors import PluginLoadError
from invariant.spec.plugin import BasePlugin
from invariant.spec.source import PluginSourceSpec

logger = logging.getLogger(__name__)


class ZipSource(PluginSource):
    """
    Zip-based source.

    Stub implementation:
    - discover() returns empty list
    - load() raises PluginLoadError

    When implemented, this should support loading plugin modules from:
    - local zip file path (source.path)
    - remote zip URL (source.url)
    with integrity checks (checksum) and safe extraction.
    """

    kind = "zip"

    def __init__(self, search_paths: list[str] | None = None):
        self.search_paths = list(search_paths or [])

    def discover(
        self,
        base_class: type | None = None,
        include_unstable: bool = False,
        *,
        dev_mode: bool = False,
        fail_fast: bool = False,
        **kwargs: Any,
    ) -> list[PluginDescriptor]:
        logger.debug("[ZipSource] discover() stub")
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
            "ZipSource is not implemented yet",
            meta_id=getattr(getattr(descriptor, "meta", None), "id", None),
        )
