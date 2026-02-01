from __future__ import annotations

import logging
from typing import Any

from invariant.load.sources.base import PluginSource
from invariant.spec.descriptor import PluginDescriptor
from invariant.spec.errors import PluginLoadError
from invariant.spec.plugin import BasePlugin
from invariant.spec.source import PluginSourceSpec

logger = logging.getLogger(__name__)


class RemoteSource(PluginSource):
    """
    Remote catalog source.

    Stub implementation:
    - discover() returns empty list
    - load() raises PluginLoadError

    When implemented, this should:
    - query remote plugin index/catalog
    - fetch manifests/artifacts (zip/wheel/etc.)
    - validate signatures/checksums
    - provide PluginDescriptor list
    """

    kind = "api"

    def __init__(self, base_url: str | None = None, token: str | None = None):
        self.base_url = base_url
        self.token = token

    def discover(
        self,
        base_class: type | None = None,
        include_unstable: bool = False,
        *,
        dev_mode: bool = False,
        fail_fast: bool = False,
        **kwargs: Any,
    ) -> list[PluginDescriptor]:
        logger.debug("[RemoteSource] discover() stub: base_url=%r", self.base_url)
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
            "RemoteSource is not implemented yet",
            meta_id=getattr(getattr(descriptor, "meta", None), "id", None),
        )
