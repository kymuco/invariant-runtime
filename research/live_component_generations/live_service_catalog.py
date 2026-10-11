"""Research-only live catalog refresh IN ONE EXISTING PluginService.

Private invariant.catalog.registry._descriptors publication is an experiment,
NOT a supported API. Never import this module into production runtime/core.
All graph dependencies must be explicit pinned EXECUTION refs. Add-only epochs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import RLock
from typing import Iterable

from catalog_epoch import Candidate, CatalogConflict, CatalogEpochs, CatalogPlan
from prototype import ProviderSlot, ProviderSpec


def _fingerprint(descriptor: object) -> str:
    source = descriptor.source
    extra = source.extra or {}
    manifest = Path(extra['manifest_path']).resolve()
    code = Path(source.path).resolve()
    # Manifest loader already validates source path under the plugin root.
    payload = (str(manifest), hashlib.sha256(manifest.read_bytes()).hexdigest(),
               str(code), hashlib.sha256(code.read_bytes()).hexdigest())
    return hashlib.sha256(json.dumps(payload).encode('utf-8')).hexdigest()


class LiveServiceCatalog:
    """Monotonic research refresh on the SAME service and registry object.

    Validation uses the real loader/resolver in a detached registry before a
    single descriptor-map pointer swap. Existing instances are untouched.
    Concurrent unpinned execution is NOT generation-isolated.
    """

    def __init__(self, service: object, *, roots: Iterable[Path]):
        self.service = service
        self.roots = tuple(Path(p).resolve() for p in roots)
        if not self.roots:
            raise ValueError('roots required')
        self._lock = RLock()
        initial = self._discover()
        registry = service.registry
        with registry._lock:
            existing = self._registry_descriptors(registry)
            if set(existing) != {x.key for x in initial}:
                raise CatalogConflict('initial service registry differs from disk catalog')
            self._installed_refs = existing
        self._epochs = CatalogEpochs(initial)

    @staticmethod
    def _registry_descriptors(registry: object) -> dict[str, object]:
        return {f'{d.meta.id}@{d.meta.version}': d
                for versions in registry._descriptors.values() for d in versions.values()}

    @property
    def epoch(self) -> int:
        return self._epochs.epoch

    def _discover(self) -> tuple[Candidate, ...]:
        # Internal imports are ONLY legal inside this research experiment.
        from invariant.catalog.registry import PluginRegistry
        from invariant.exec.caps.registry import is_capability_ref
        from invariant.exec.runtime.resolver import DependencyResolver
        from invariant.spec.dependency import DependencyScope
        from invariant.spec.versioning import canon_version_key

        # Loader._dedupe can resolve duplicate identities silently.
        seen_raw: set[str] = set()
        for root in self.roots:
            for manifest in sorted(root.rglob('manifest.json')):
                data = json.loads(manifest.read_text(encoding='utf-8'))
                if data.get('maturity', 'stable') != 'stable':
                    continue
                key = f"{data['id']}@{canon_version_key(data['version'])}"
                if key in seen_raw:
                    raise CatalogConflict(f'duplicate manifest identity: {key}')
                seen_raw.add(key)

        descriptors = self.service.loader.discover(
            mode='strict', fail_fast=True, include_unstable=False)
        detached = PluginRegistry(mode='strict')
        entries = []
        for d in descriptors:
            if d.meta.version is None:
                raise CatalogConflict('unversioned plugin in strict mode')
            detached.register(d)
            deps = []
            for spec in d.meta.dependencies:
                if is_capability_ref(spec.id):
                    continue
                if spec.scope is not DependencyScope.EXECUTION or spec.optional:
                    raise CatalogConflict('research requires nonoptional EXECUTION dependencies')
                if '@' not in spec.id or spec.id.endswith('@latest'):
                    raise CatalogConflict(f'unpinned dependency: {spec.id}')
                deps.append(spec.id)
            extra = d.source.extra or {}
            if (extra.get('deps') or {}).get('mode', 'none') != 'none':
                raise CatalogConflict('isolated plugin dependencies outside this pilot')
            key = f'{d.meta.id}@{d.meta.version}'
            entries.append(Candidate(key, _fingerprint(d), tuple(deps), d))
        # Resolve all graphs before changing the service registry.
        resolver = DependencyResolver(registry=detached)
        for entry in entries:
            resolver.collect(entry.key)
        if {x.key for x in entries} != seen_raw:
            raise CatalogConflict('discovery/manifest identity mismatch')
        return tuple(entries)

    def stage(self) -> CatalogPlan:
        with self._lock:
            return self._epochs.stage(self._discover())

    def publish(self, plan: CatalogPlan) -> int:
        with self._lock:
            fresh = self._discover()
            registry = self.service.registry

            def commit(entries, added):
                with registry._lock:
                    current = self._registry_descriptors(registry)
                    if set(current) != set(self._installed_refs) or any(
                        current[k] is not self._installed_refs[k] for k in current
                    ):
                        raise CatalogConflict('registry changed outside live-catalog coordinator')
                    staged = {k: dict(v) for k, v in registry._descriptors.items()}
                    for key in added:
                        d = entries[key].payload
                        versions = staged.setdefault(d.meta.id, {})
                        if d.meta.version in versions:
                            raise CatalogConflict(f'version already registered: {key}')
                        versions[d.meta.version] = d
                    # Shared lock makes this a single catalog visibility point.
                    registry._descriptors = staged
                    self._installed_refs = self._registry_descriptors(registry)
                    # registry._instances deliberately remains unchanged.

            return self._epochs.publish(plan, fresh, commit=commit)


def register_live_version(slot: ProviderSlot, catalog: LiveServiceCatalog,
                          plugin_id: str, version: str, *, method: str = 'synthesize') -> None:
    """One stable host binding, multiple execution contexts, ONE shared service."""
    from invariant.api import EphemeralPolicy
    ref = f'{plugin_id}@{version}'
    if ref not in catalog._epochs.snapshot()[1]:
        raise CatalogConflict('version is not published')

    def start():
        context = catalog.service.execution(policy=EphemeralPolicy())
        try:
            plugin = context.get(ref)
            if not callable(getattr(plugin, method, None)):
                raise TypeError('host contract method is missing')
        except BaseException:
            context.close()
            raise
        return (context, plugin)

    def stop(value):
        value[0].close()

    slot.install(ProviderSpec(slot.contract, plugin_id, version, start, stop))
