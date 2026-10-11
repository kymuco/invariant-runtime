"""Research-only opt-in execution pinned to a real immutable CatalogView.

This bridge deliberately wires existing private ExecutionContext plumbing.
It is NOT a public Invariant API, in-place service refresh, a lease, or a
general plugin scheduler. Supported scope: mandatory EXECUTION dependencies
under EphemeralPolicy. It keeps loader and capability policy unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass

from catalog_epoch import Candidate, CatalogConflict
from gate_a_catalog_view import CatalogView
from live_service_catalog import _fingerprint


def capture_catalog_view(service: object, *, epoch: int = 1) -> CatalogView:
    """Freeze the current service descriptor inventory and dependency selections.

    Registry.list() is a copy under its own lock. Resolution is done only
    against a detached registry, so a concurrent live registry mutation
    cannot mix dependency versions within this capture. Descriptor source
    files are still host-owned mutable files; this is not an artifact store.
    """
    from invariant.catalog.registry import PluginRegistry
    from invariant.exec.caps.registry import is_capability_ref
    from invariant.spec.dependency import DependencyScope

    descriptors = tuple(service.registry.list())
    if not descriptors:
        raise CatalogConflict("empty catalog is outside this research bridge")

    detached = PluginRegistry(mode="strict")
    for descriptor in descriptors:
        detached.register(descriptor)

    candidates: list[Candidate] = []
    for descriptor in descriptors:
        key = f"{descriptor.meta.id}@{descriptor.meta.version}"
        dependencies: list[str] = []
        for spec in descriptor.meta.dependencies:
            if is_capability_ref(spec.id):
                # Permission-bearing capabilities still go through the real
                # ExecutionRuntime policy-gated capability view.
                continue
            if spec.optional or spec.scope is not DependencyScope.EXECUTION:
                raise CatalogConflict(
                    "pinned research activation only supports mandatory "
                    f"EXECUTION dependencies: {key} -> {spec.id}"
                )
            resolved = detached.resolve(spec.id)
            dependencies.append(f"{resolved.meta.id}@{resolved.meta.version}")
        candidates.append(
            Candidate(key, _fingerprint(descriptor), tuple(dependencies), descriptor)
        )
    return CatalogView(epoch, candidates)


@dataclass(slots=True)
class _PinnedService:
    """Minimum private ExecutionContext bridge; not an exported host facade."""

    registry: CatalogView
    loader: object
    tracer: object


def open_pinned_execution(service: object, view: CatalogView):
    """Open a real ephemeral ExecutionContext bound to exactly one view.

    All normal activation lookups, including ActivationTransaction injection,
    resolve via view. Other live service callers are unaffected. The caller
    owns closing the returned context and must not retain raw plugin objects
    beyond that context's lifetime.
    """
    from invariant.api import EphemeralPolicy
    from invariant.exec.runtime.execution_context import ExecutionContext
    from invariant.exec.caps.registry import is_capability_ref
    from invariant.spec.dependency import DependencyScope

    # Also reject unsupported scopes when handed a view built elsewhere.
    for candidate in view.entries.values():
        for spec in candidate.payload.meta.dependencies:
            if is_capability_ref(spec.id):
                continue
            if spec.optional or spec.scope is not DependencyScope.EXECUTION:
                raise CatalogConflict(
                    "pinned research activation only supports mandatory EXECUTION"
                )
    bound = _PinnedService(
        registry=view,
        loader=service.loader,
        tracer=getattr(service, "tracer", None),
    )
    return ExecutionContext(plugin_service=bound, policy=EphemeralPolicy())
