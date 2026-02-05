# invariant/load/loader.py
from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping

from invariant.load.env.env_manager import EnvManager
from invariant.load.isolated_proxy import IsolatedPluginProxy
from invariant.load.sources.base import PluginSource
from invariant.spec.deps import PluginDepsSpec
from invariant.spec.descriptor import PluginDescriptor
from invariant.spec.errors import PluginLoadError
from invariant.spec.plugin import BasePlugin
from invariant.spec.versioning import canon_version_key

logger = logging.getLogger(__name__)


class PluginLoader:
    """
    Facade over multiple plugin sources (filesystem, manifest, remote, etc.).

    Responsibilities:
    - discover PluginDescriptor from all sources
    - deterministic de-duplication
    - load plugin instance via the first suitable source

    NOT responsible for:
    - assigning runtime versions (0+local.*)  -> registry owns this
    - lifecycle / dependency resolution
    """

    def __init__(self, sources: list[PluginSource], *, env_manager: EnvManager | None = None):
        self.sources = sources
        self._env_manager = env_manager or EnvManager()

    def discover(
        self,
        base_class: type | None = None,
        include_unstable: bool = False,
        *,
        mode: str = "strict",   # "strict" | "dev"
        fail_fast: bool = False,   # fail-fast discover (per-source errors)
    ) -> list[PluginDescriptor]:
        """
        Discover plugins across all sources and return PluginDescriptor list.

        mode:
          - "strict": versionless plugins are NOT allowed (discover may raise)
          - "dev":    versionless plugins are allowed (sources may accept dev_mode=True)

        fail_fast:
          - if True: any source failure raises
          - if False: failures are logged and skipped
        """
        m = (mode or "").strip().lower()
        if m not in {"strict", "dev"}:
            raise ValueError("mode must be 'strict' or 'dev'")
        dev_mode = (m == "dev")

        descriptors: list[PluginDescriptor] = []

        for src in self.sources:
            try:
                found = src.discover(
                    base_class=base_class,
                    include_unstable=include_unstable,
                    dev_mode=dev_mode,
                    fail_fast=fail_fast,
                )

                for d in found:
                    if not isinstance(d, PluginDescriptor):
                        raise TypeError(
                            f"{src}.discover() returned {type(d)}, expected PluginDescriptor"
                        )

                descriptors.extend(found)

            except Exception:
                logger.exception("Source %s failed during discover()", src)
                if fail_fast:
                    raise

        return self._dedupe(descriptors, mode=m)

    def load(self, descriptor: PluginDescriptor, registry=None) -> BasePlugin:
        """
        Load a plugin via the first suitable source.
        """
        deps = self._deps_from_descriptor(descriptor)
        if deps is not None and deps.is_isolated():
            logger.info("Loading isolated plugin %s mode=%s", descriptor.meta.id, deps.mode)
            return IsolatedPluginProxy.from_descriptor(
                descriptor,
                env_manager=self._env_manager,
            )
        
        for src in self.sources:
            if src.can_load(descriptor.source):
                return src.load(descriptor.source, registry, descriptor=descriptor)

        raise PluginLoadError(
            f"No source can load plugin {descriptor.meta.id}@{descriptor.meta.version} "
            f"(available sources: {[getattr(s, 'kind', None) for s in self.sources]})",
            meta_id=descriptor.meta.id,
        )
    
    def _deps_from_descriptor(self, d: PluginDescriptor) -> PluginDepsSpec | None:
        extra = getattr(d.source, "extra", None) or {}
        raw = extra.get("deps")
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            # malformed manifest; treat as hard error
            raise PluginLoadError(f"Invalid deps block in descriptor.source.extra for {d.meta.id}", meta_id=d.meta.id)
        # normalize MappingProxyType -> dict
        return PluginDepsSpec.from_manifest(dict(raw))

    def _dedupe(self, descriptors: list[PluginDescriptor], *, mode: str) -> list[PluginDescriptor]:
        """
        Deterministic de-duplication.

        Keys:
          - versioned:   (meta.id, canon_version)
          - unversioned (dev only): (meta.id, "<unversioned>", source_fingerprint)

        Winner selection rules (in order):
          1) Higher meta.priority wins
          2) Preferred source.kind wins (configurable ranking)
          3) Stable tie-breaker (lexicographic by source.kind, then meta.describe)
        """

        SOURCE_RANK: dict[str, int] = {
            "manifest": 50,
            "entrypoint": 40,
            "filesystem": 30,
            "zip": 20,
            "api": 10,
        }

        unique: dict[tuple, PluginDescriptor] = {}

        def canon_version_or_raise(ver: str, *, meta_id: str) -> str:
            try:
                return canon_version_key(ver)
            except ValueError as e:
                raise PluginLoadError(
                    f"Bad version format for {meta_id}: {ver!r}",
                    meta_id=meta_id,
                    cause=e,
                ) from e

        def source_fingerprint(d: PluginDescriptor) -> str:
            """
            Stable fingerprint for versionless plugin de-duplication in dev mode.
            IMPORTANT: This is NOT the runtime version key. Registry owns 0+local.*.
            """
            s = d.source
            parts = [
                d.meta.id,
                getattr(s, "kind", "") or "",
                str(getattr(s, "module", "") or ""),
                str(getattr(s, "class_name", "") or ""),
                str(getattr(s, "entry_point", "") or ""),
                str(getattr(s, "path", "") or ""),
                str(getattr(s, "url", "") or ""),
                str(getattr(s, "checksum", "") or ""),
            ]
            return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]

        def score(d: PluginDescriptor) -> tuple[int, int, str, str, str]:
            pri = int(getattr(d.meta, "priority", 0) or 0)
            kind = getattr(d.source, "kind", "unknown") or "unknown"
            rank = SOURCE_RANK.get(kind, 0)
            meta_sig = d.meta.describe() if hasattr(d.meta, "describe") else f"{d.meta.id}@{d.meta.version}"
            fp = source_fingerprint(d)
            # fp as final tie-break => deterministic winner
            return (pri, rank, kind, meta_sig, fp)
        
        def version_label_for(d: PluginDescriptor) -> str:
            if d.meta.version is None:
                return "<unversioned>"
            return canon_version_or_raise(d.meta.version, meta_id=d.meta.id)

        def descriptor_sort_key(d: PluginDescriptor) -> tuple[str, str, str, str]:
            """
            Stable ordering BEFORE de-duplication to eliminate order-dependence.
            """
            kind = getattr(d.source, "kind", "unknown") or "unknown"
            return (
                d.meta.id,
                version_label_for(d),
                kind,
                source_fingerprint(d),
            )

        for d in sorted(descriptors, key=descriptor_sort_key):
            meta = d.meta

            if meta.version is None:
                if mode == "strict":
                    raise PluginLoadError(
                        f"Plugin {meta.id} must declare version in strict mode",
                        meta_id=meta.id,
                    )
                key = (meta.id, "<unversioned>", source_fingerprint(d))
                version_label = "<unversioned>"
            else:
                vkey = canon_version_or_raise(meta.version, meta_id=meta.id)
                key = (meta.id, vkey, "")
                version_label = vkey

            prev = unique.get(key)
            if prev is None:
                unique[key] = d
                continue

            prev_s = score(prev)
            new_s = score(d)

            if new_s > prev_s:
                logger.warning(
                    "Duplicate plugin replaced: %s v=%s (winner=%s/%s, loser=%s/%s)",
                    meta.id,
                    version_label,
                    getattr(d.source, "kind", None),
                    getattr(d.meta, "priority", None),
                    getattr(prev.source, "kind", None),
                    getattr(prev.meta, "priority", None),
                )
                unique[key] = d
            else:
                logger.warning(
                    "Duplicate plugin ignored: %s v=%s (kept=%s/%s, ignored=%s/%s)",
                    meta.id,
                    version_label,
                    getattr(prev.source, "kind", None),
                    getattr(prev.meta, "priority", None),
                    getattr(d.source, "kind", None),
                    getattr(d.meta, "priority", None),
                )

        return sorted(unique.values(), key=descriptor_sort_key)
