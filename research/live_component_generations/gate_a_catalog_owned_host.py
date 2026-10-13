"""Research-only host whose ONE catalog pointer owns activation visibility.

Unlike LiveServiceCatalog, this experiment does not keep a separately
mutable PluginService registry next to CatalogViews. A real PluginLoader
supplies metadata and Python instances; each real ExecutionContext receives
the SAME retained CatalogView for every descriptor/dependency lookup.

The host is NOT a PluginService, a production facade or a public API.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from catalog_epoch import Candidate, CatalogConflict
from gate_a_catalog_view import CatalogViews, CatalogView, Stage
from gate_a_pinned_execution import open_pinned_execution
from live_service_catalog import _fingerprint


def research_manifest_loader(roots: tuple[Path, ...]):
    """Share one EnvManager for manifest discovery and runtime loading."""
    from invariant.load.env.env_manager import EnvManager
    from invariant.load.loader import PluginLoader
    from invariant.load.sources.manifest import ManifestSource

    env_manager = EnvManager()
    return PluginLoader(
        [ManifestSource(list(roots), env_manager=env_manager)],
        env_manager=env_manager,
    )


def discover_candidates(roots: tuple[Path, ...], loader: object) -> tuple[Candidate, ...]:
    from invariant.catalog.registry import PluginRegistry
    from invariant.exec.caps.registry import is_capability_ref
    from invariant.spec.dependency import DependencyScope
    from invariant.spec.versioning import canon_version_key

    # PluginLoader silently de-duplicates identical version keys; fail
    # closed before discovery rather than hiding a conflicting artifact.
    seen: set[str] = set()
    for root in roots:
        for manifest in sorted(root.rglob("manifest.json")):
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if data.get("maturity", "stable") != "stable":
                continue
            key = f"{data['id']}@{canon_version_key(data['version'])}"
            if key in seen:
                raise CatalogConflict(f"duplicate manifest identity: {key}")
            seen.add(key)

    descriptors = tuple(loader.discover(
        mode="strict", fail_fast=True, include_unstable=False
    ))
    registry = PluginRegistry(mode="strict")
    for descriptor in descriptors:
        registry.register(descriptor)

    candidates: list[Candidate] = []
    for descriptor in descriptors:
        key = f"{descriptor.meta.id}@{descriptor.meta.version}"
        dependencies: list[str] = []
        for spec in descriptor.meta.dependencies:
            if is_capability_ref(spec.id):
                continue
            if spec.optional or spec.scope is not DependencyScope.EXECUTION:
                raise CatalogConflict(
                    f"unsupported research dependency scope: {key} -> {spec.id}"
                )
            resolved = registry.resolve(spec.id)
            dependencies.append(f"{resolved.meta.id}@{resolved.meta.version}")
        candidates.append(
            Candidate(key, _fingerprint(descriptor), tuple(dependencies), descriptor)
        )
    if {item.key for item in candidates} != seen:
        raise CatalogConflict("loader inventory differs from raw manifests")
    return tuple(candidates)



class CatalogOwnedHost:
    """Single catalog source for opt-in ephemeral research executions."""

    def __init__(self, roots: Iterable[Path]):
        self.roots = tuple(Path(root).resolve() for root in roots)
        if not self.roots:
            raise ValueError("roots required")
        self.loader = research_manifest_loader(self.roots)
        self.tracer = None
        self._views = CatalogViews(self._discover())

    def _discover(self) -> tuple[Candidate, ...]:
        return discover_candidates(self.roots, self.loader)

    def snapshot(self) -> CatalogView:
        return self._views.snapshot()

    def stage(self) -> Stage:
        return self._views.stage(self._discover())

    def publish(self, stage: Stage) -> CatalogView:
        return self._views.publish(stage, self._discover())

    def execution(self):
        """Open a real execution pinned to ONE host catalog snapshot.

        Only the caller owns context.close(). The returned context does not
        acquire a tracked generation lease or make untrusted code safe.
        """
        return open_pinned_execution(self, self.snapshot())
