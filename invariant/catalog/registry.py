# invariant/catalog/registry.py
from __future__ import annotations

import builtins
import hashlib
import logging
import threading
from collections.abc import Iterable
from dataclasses import replace

from packaging.version import InvalidVersion, Version

from invariant.spec.descriptor import PluginDescriptor
from invariant.spec.errors import (
    AmbiguousPluginReference,
    PluginLoadError,
    PluginNotFoundError,
)
from invariant.spec.ids import base_id, canonical_plugin_id, split_id_version
from invariant.spec.meta import PluginMeta
from invariant.spec.versioning import canon_version_key

logger = logging.getLogger(__name__)


class PluginRegistry:
    """
    Чистый реестр плагинов.

    Ответственность:
    - хранение PluginDescriptor
    - разрешение версий (semver / latest)
    - хранение инстансов по opaque key

    НЕ отвечает за:
    - lifecycle
    - зависимости
    - DI
    - Loader
    """
    _LATEST_ALIAS = "latest"
    _LOCAL_PREFIX = "0.0.0+local."

    def __init__(
        self,
        *,
        mode: str = "strict",
        dev_allow_unversioned_kinds: Iterable[str] | None = None,
    ) -> None:
        self._lock = threading.RLock()

        # plugin_id -> {version -> PluginDescriptor}
        self._descriptors: dict[str, dict[str, PluginDescriptor]] = {}

        # opaque_key -> instance
        self._instances: dict[str, object] = {}

        m = (mode or "").strip().lower()
        if m not in {"strict", "dev"}:
            raise ValueError("mode must be 'strict' or 'dev'")
        self._mode = m

        # In dev: allow versionless plugins only from these source kinds
        self._dev_allow_unversioned_kinds: set[str] = set(
            dev_allow_unversioned_kinds or {"filesystem"}
        )

        # Prevent log spam for invalid stored version keys
        self._invalid_version_keys_logged: set[tuple[str, str]] = set()  # (plugin_id, version_key)

    # =====================================================
    # VERSIONING / IDS
    # =====================================================

    @staticmethod
    def _canon_version(ver: str) -> str:
        # canon_version_key should already enforce stable keying (PEP440-ish)
        return canon_version_key(ver)

    @classmethod
    def _parse_ref(cls, plugin_ref: str) -> tuple[str, str | None]:
        """
        Parse 'id', 'id@version', 'id@latest'.
        Returns canonical (base_id, version_key_or_None).
        If version is None -> selector (pick highest).
        """
        if not isinstance(plugin_ref, str) or not plugin_ref.strip():
            raise ValueError("plugin_id must be non-empty string")

        base, ver = split_id_version(plugin_ref.strip())
        base_id = canonical_plugin_id(base)

        if ver is None:
            return base_id, None

        if ver.strip().lower() == cls._LATEST_ALIAS:
            return base_id, None  # alias -> selector

        return base_id, cls._canon_version(ver.strip())

    @staticmethod
    def _local_version_for(descriptor: PluginDescriptor) -> str:
        """
        Deterministic PEP440 local version for unversioned plugins in dev mode.
        Same artifact -> same local version.
        """
        s = descriptor.source
        parts = [
            descriptor.meta.id,
            s.kind,
            str(s.module or ""),
            str(s.class_name or ""),
            str(s.entry_point or ""),
            str(s.path or ""),
            str(s.url or ""),
            str(s.checksum or ""),
        ]
        h = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
        return f"{PluginRegistry._LOCAL_PREFIX}{h}"

    @classmethod
    def _is_local_version_key(cls, v: str) -> bool:
        return isinstance(v, str) and v.startswith(cls._LOCAL_PREFIX)


    # =====================================================
    # DESCRIPTORS
    # =====================================================

    def register(self, descriptor: PluginDescriptor) -> None:
        if descriptor is None:
            raise ValueError("descriptor must not be None")
        
        meta = descriptor.meta
        # plugin_id = canonical_plugin_id(meta.id)
        # meta.id may be provided as "id@version" by callers; registry keys must be base id.
        plugin_id = base_id(meta.id)

        # --- prepare OUTSIDE lock (avoid blocking resolve/list) ---
        if meta.version is None:
            if self._mode == "strict":
                raise PluginLoadError(
                    f"Plugin {plugin_id} must declare 'version' in strict mode",
                    meta_id=plugin_id,
                )

            # dev mode
            if descriptor.source.kind not in self._dev_allow_unversioned_kinds:
                raise PluginLoadError(
                    f"Plugin {plugin_id} without version is not allowed from source kind={descriptor.source.kind!r} in dev mode",
                    meta_id=plugin_id,
                )

            version = self._local_version_for(descriptor)
            warn_unversioned = True
        else:
            version = self._canon_version(meta.version)
            warn_unversioned = False

        # Optional: In strict mode, reject invalid PEP440 keys early.
        # canon_version_key SHOULD handle this, but registry is the last line of defense.
        if self._mode == "strict":
            try:
                Version(version)
            except InvalidVersion as e:
                raise PluginLoadError(
                    f"Invalid plugin version {meta.version!r} (normalized={version!r}): {e}",
                    meta_id=plugin_id,
                ) from e

        # normalize stored meta.version to match registry key
        if meta.version != version:
            meta = replace(meta, version=version)
            descriptor = PluginDescriptor(meta=meta, source=descriptor.source)

        # --- commit under lock ---
        with self._lock:
            vmap = self._descriptors.setdefault(plugin_id, {})
            if version in vmap:
                logger.warning("[Registry] Overwriting %s@%s", plugin_id, version)
            vmap[version] = descriptor

        if warn_unversioned:
            logger.warning(
                "[Registry] %s registered without version (dev); assigned %s",
                plugin_id,
                version,
            )
        logger.debug("[Registry] Registered %s@%s", plugin_id, version)

    def resolve(self, plugin_id: str) -> PluginDescriptor:
        """
        plugin_id:
          - author:name
          - author:name@1.2.3
          - author:name@latest   (alias -> selector)
        """
        base_id, version = self._parse_ref(plugin_id)

        with self._lock:
            versions = self._descriptors.get(base_id)
            if not versions:
                raise PluginNotFoundError(f"Plugin not found: {base_id}", meta_id=base_id)

            if version is not None:
                descriptor = versions.get(version)
                if not descriptor:
                    raise PluginNotFoundError(f"Version not found: {plugin_id}", meta_id=base_id)
                return descriptor

            # selector: pick highest non-local version if exists; otherwise handle locals carefully
            non_local_keys = [v for v in versions.keys() if not self._is_local_version_key(v)]
            local_keys = [v for v in versions.keys() if self._is_local_version_key(v)]

            if non_local_keys:
                subset = {v: versions[v] for v in non_local_keys}
                descriptor = self._pick_highest_version(versions=subset, plugin_id=base_id)
                if not descriptor:
                    raise PluginNotFoundError(f"Plugin not found: {base_id}", meta_id=base_id)
                return descriptor

            # only local versions exist
            if len(local_keys) == 1:
                return versions[local_keys[0]]

            if len(local_keys) > 1:
                local_sorted = sorted(local_keys)
                sample = ", ".join(local_sorted[:5])
                raise AmbiguousPluginReference(
                    f"Ambiguous plugin reference {base_id!r}: multiple local builds exist ({sample}...). "
                    f"Specify exact version as {base_id}@<version> (choose one of the listed local builds).",
                    meta_id=base_id,
                )

            raise PluginNotFoundError(f"Plugin not found: {base_id}", meta_id=base_id)

    def list(self, *, category: str | None = None) -> builtins.list[PluginDescriptor]:
        # Snapshot under lock, filter outside lock
        with self._lock:
            items = [d for vmap in self._descriptors.values() for d in vmap.values()]

        if not category:
            return items
        return [d for d in items if d.meta.matches_category(category)]
    
    def get_meta(self, plugin_id: str) -> PluginMeta:
        return self.resolve(plugin_id).meta

    def list_meta(self, *, category: str | None = None) -> builtins.list[PluginMeta]:
        return [d.meta for d in self.list(category=category)]


    # =====================================================
    # INSTANCES (managed cache)
    # =====================================================

    def instances(self) -> dict[str, object]:
        """
        Copy of managed instances cache.
        Key is canonical instance key (decided by runtime).
        """
        with self._lock:
            return dict(self._instances)

    def get_instance(self, key: str):
        with self._lock:
            return self._instances.get(str(key))

    def set_instance(self, key: str, instance: object) -> None:
        with self._lock:
            self._instances[str(key)] = instance

    def drop_instance(self, key: str) -> None:
        with self._lock:
            self._instances.pop(str(key), None)

    def clear_instances(self) -> None:
        with self._lock:
            self._instances.clear()

    # =====================================================
    # MAINTENANCE
    # =====================================================

    def clear(self) -> None:
        with self._lock:
            self._descriptors.clear()
            self._instances.clear()
            self._invalid_version_keys_logged.clear()
        logger.info("[Registry] Cleared")

    # =====================================================
    # HELPERS
    # =====================================================

    # @staticmethod
    # def _split_id(plugin_id: str):
    #     if "@" in plugin_id:
    #         return plugin_id.split("@", 1)
    #     return plugin_id, None

    # @staticmethod
    def _pick_highest_version(
        self,
        versions: dict[str, PluginDescriptor],
        *,
        plugin_id: str,
    ) -> PluginDescriptor | None:
        candidates: list[tuple[Version, PluginDescriptor]] = []

        for v, d in versions.items():
            try:
                candidates.append((Version(v), d))
            except InvalidVersion:
                # Avoid spamming logs on hot path
                k = (plugin_id, str(v))
                if k not in self._invalid_version_keys_logged:
                    # prevent unbounded growth in pathological cases
                    if len(self._invalid_version_keys_logged) > 1024:
                        self._invalid_version_keys_logged.clear()
                    self._invalid_version_keys_logged.add(k)
                    logger.warning(
                        "[Registry] Invalid version key in registry: %r (plugin=%s)",
                        v,
                        plugin_id,
                    )
                continue

        if not candidates:
            return None

        return max(candidates, key=lambda x: x[0])[1]
