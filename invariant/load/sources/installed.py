"""Verified installed plugin objects are runtime sources, not ZIP archives.

LocalPluginStore owns acquisition, publication and leases. An installed object
always runs from its manifest, regardless of whether it arrived as a ZIP,
URL, or any future distribution format. No downloads/extractions occur here.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from invariant.load.sources.manifest import ManifestSource
from invariant.spec.deps import PluginDepsSpec
from invariant.spec.descriptor import PluginDescriptor
from invariant.spec.errors import PluginLoadError
from invariant.spec.source import PluginSourceSpec
from invariant.spec.versioning import canon_version_key

if TYPE_CHECKING:
    from invariant.load.plugin_store import LocalPluginStore

logger = logging.getLogger(__name__)


class InstalledSource(ManifestSource):
    """Discover only root manifests from a lease-pinned set of Store objects.

    Validation runs again on the selected object before in-process or isolated
    activation. This is an internal source; it is not a hostile-code sandbox.
    """

    kind = "installed"
    handles_isolated_load = True

    def __init__(self, store: LocalPluginStore, roots: Iterable[Path]):
        super().__init__([])
        self.store = store
        self._roots = frozenset(Path(root).resolve() for root in roots)
        if any(root.parent != self.store.objects.resolve() for root in self._roots):
            raise PluginLoadError("installed plugin root outside PluginStore")

    def discover(
        self,
        base_class: type | None = None,
        include_unstable: bool = False,
        *,
        dev_mode: bool = False,
        fail_fast: bool = False,
        **kwargs,
    ) -> list[PluginDescriptor]:
        descriptors: list[PluginDescriptor] = []
        for root in sorted(self._roots):
            try:
                self.store._verify_object(root.name)
                descriptor = self._read_manifest(root / "manifest.json", dev_mode=dev_mode)
                if not include_unstable and descriptor.meta.maturity != "stable":
                    continue
                if base_class is not None:
                    deps = PluginDepsSpec.from_manifest(descriptor.source.extra.get("deps"))
                    if deps.is_isolated():
                        continue
                    source = descriptor.source
                    module = self._load_module(source.module, source.path)
                    cls = getattr(module, source.class_name, None)
                    if not isinstance(cls, type) or not issubclass(cls, base_class):
                        continue
                descriptors.append(descriptor)
            except Exception:
                logger.exception("Invalid installed plugin object: %s", root)
                if fail_fast:
                    raise
        return descriptors

    def can_load(self, source: PluginSourceSpec) -> bool:
        if source.kind != self.kind:
            return False
        root = source.extra.get("plugin_root")
        return isinstance(root, str) and Path(root).resolve() in self._roots

    def load(self, source: PluginSourceSpec, registry=None, *, descriptor=None, **kwargs):
        if descriptor is None or not self.can_load(source):
            raise PluginLoadError("descriptor does not belong to installed Store snapshot")
        root = Path(source.extra["plugin_root"]).resolve()
        self.store._verify_object(root.name)
        canonical = self._read_manifest(root / "manifest.json", dev_mode=False)
        if (
            replace(descriptor.meta, version=canon_version_key(descriptor.meta.version))
            != replace(canonical.meta, version=canon_version_key(canonical.meta.version))
            or source != canonical.source
        ):
            raise PluginLoadError("installed plugin descriptor differs from verified manifest")
        return super().load(
            canonical.source, registry, descriptor=replace(canonical, meta=descriptor.meta),
        )
