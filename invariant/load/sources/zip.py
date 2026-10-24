"""Runtime source for ZIP-origin plugins already installed in LocalPluginStore.

Acquisition and installation are explicit Store operations. This source never
extracts, downloads, or installs in discover() or load(). The original ZIP may
be deleted after installation; immutable materialized plugin files are loaded
through the existing ManifestSource implementation.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from invariant.load.sources.base import PluginSource
from invariant.load.sources.manifest import ManifestSource
from invariant.spec.deps import PluginDepsSpec
from invariant.spec.descriptor import PluginDescriptor
from invariant.spec.errors import PluginLoadError
from invariant.spec.plugin import BasePlugin
from invariant.spec.source import PluginSourceSpec
from invariant.spec.versioning import canon_version_key

if TYPE_CHECKING:
    from invariant.load.env.env_manager import EnvManager
    from invariant.load.plugin_store import LocalPluginStore

logger = logging.getLogger(__name__)


class ZipSource(PluginSource):
    """Discover installed ZIP artifacts via the normal PluginLoader contract.

    ZIP inputs are not discovered here. Only explicit Store installations are
    eligible. An optional snapshot is pinned by LocalPluginStore.open_service().
    """

    kind = "zip"
    handles_isolated_load = True

    def __init__(
        self,
        search_paths: list[str] | None = None,
        *,
        store: LocalPluginStore | None = None,
        installed_roots: Iterable[Path] | None = None,
        env_manager: EnvManager | None = None,
    ) -> None:
        if search_paths:
            raise ValueError("Install local ZIP paths explicitly with LocalPluginStore first")
        if installed_roots is not None and store is None:
            raise ValueError("installed_roots requires a LocalPluginStore")
        self.store = store
        self._snapshot = tuple(Path(path) for path in installed_roots) if installed_roots is not None else None
        self.env_manager = env_manager
        # Populated by discover(): used for ownership-aware source dispatch.
        self._known_roots: frozenset[Path] = frozenset()

    def _roots(self) -> list[Path]:
        if self.store is None:
            return []
        if self._snapshot is None:
            return self.store.plugin_roots()
        roots = list(self._snapshot)
        for root in roots:
            if root.parent.resolve() != self.store.objects.resolve():
                raise PluginLoadError("installed ZIP root must belong to this PluginStore")
            # A pinned object remains even after a concurrent logical uninstall.
            # Verify by digest; catalog membership need not be current.
            self.store._verify_object(root.name)
        return roots

    def discover(
        self,
        base_class: type | None = None,
        include_unstable: bool = False,
        *,
        dev_mode: bool = False,
        fail_fast: bool = False,
        **kwargs: Any,
    ) -> list[PluginDescriptor]:
        result: list[PluginDescriptor] = []
        roots = self._roots()
        # Update ownership even for an empty discovery. Individual artifact
        # identity is still verified on load, but unrelated objects need not
        # be rehashed with every plugin activation.
        self._known_roots = frozenset(root.resolve() for root in roots)
        for root in roots:
            parser = ManifestSource([root], env_manager=self.env_manager)
            try:
                # One installed object = one plugin. Nested manifests inside
                # an artifact must not become additional plugin registrations.
                descriptor = parser._read_manifest(root / "manifest.json", dev_mode=dev_mode)
                if not include_unstable and descriptor.meta.maturity != "stable":
                    continue
                if base_class is not None:
                    deps = PluginDepsSpec.from_manifest((descriptor.source.extra or {}).get("deps"))
                    if deps.is_isolated():
                        continue
                    source = descriptor.source
                    if not source.module or not source.path or not source.class_name:
                        raise PluginLoadError("incomplete installed ZIP plugin source")
                    module = parser._load_module(source.module, source.path)
                    plugin_cls = getattr(module, source.class_name, None)
                    if not isinstance(plugin_cls, type) or not issubclass(plugin_cls, base_class):
                        continue
                # PluginLoader can now distinguish ZIP-origin installations;
                # 'path' is the materialized code path, not the discarded ZIP.
                result.append(replace(descriptor, source=replace(descriptor.source, kind=self.kind)))
            except Exception:
                logger.exception("Invalid installed ZIP plugin: %s", root)
                if fail_fast:
                    raise
        return result

    def can_load(self, source: PluginSourceSpec) -> bool:
        if source.kind != self.kind:
            return False
        root = (source.extra or {}).get("plugin_root")
        return isinstance(root, str) and Path(root).resolve() in self._known_roots

    def load(
        self,
        source: PluginSourceSpec,
        registry=None,
        *,
        descriptor: PluginDescriptor | None = None,
        **kwargs: Any,
    ) -> BasePlugin:
        if descriptor is None or self.store is None or not self.can_load(source):
            raise PluginLoadError("ZIP descriptor is not installed in this PluginStore")
        raw_root = (source.extra or {}).get("plugin_root")
        assert isinstance(raw_root, str)
        # Revalidate only the selected object. The full pinned snapshot was
        # verified at lease acquisition and source discovery.
        selected = Path(raw_root).resolve()
        self.store._verify_object(selected.name)
        # Reject invented code paths/classes, not just invented plugin roots.
        parser = ManifestSource([], env_manager=self.env_manager)
        canonical = parser._read_manifest(Path(raw_root) / "manifest.json", dev_mode=False)
        if (
            replace(descriptor.meta, version=canon_version_key(descriptor.meta.version))
            != replace(canonical.meta, version=canon_version_key(canonical.meta.version))
            or source.path != canonical.source.path
            or source.module != canonical.source.module
            or source.class_name != canonical.source.class_name
            or dict(source.extra) != dict(canonical.source.extra)
        ):
            raise PluginLoadError("ZIP descriptor differs from installed manifest")
        return parser.load(canonical.source, registry, descriptor=canonical)
