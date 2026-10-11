"""Research-only binding from public Invariant services to a provider slot.

Each generation has a fresh PluginService created against an immutable-by-
convention directory snapshot. We intentionally do NOT mutate the production
registry, loader, or plugin API. This proves only host-managed generations.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from invariant.api import EphemeralPolicy, PluginService
from prototype import ProviderSlot, ProviderSpec


@dataclass(frozen=True, slots=True)
class CatalogGeneration:
    contract: str  # Host-defined function label, NOT a permission capability.
    plugin_id: str
    version: str
    paths: tuple[Path, ...]
    required_method: str = "synthesize"

    def __post_init__(self) -> None:
        if not self.contract or not self.plugin_id or not self.version:
            raise ValueError("generation identity must be nonempty")
        if not self.paths:
            raise ValueError("at least one catalog root is required")
        if not self.required_method.isidentifier():
            raise ValueError("required_method must be a valid identifier")


@dataclass(slots=True)
class ActiveGeneration:
    """Explicit ownership of one execution context and its scoped graph."""

    service: PluginService
    execution: object
    provider: object
    closed: bool = False

    def close(self) -> None:
        if self.closed:
            return
        # Execution-scoped dependents are owned by the ExecutionContext, not
        # by PluginService.close(). Do not use managed SINGLETON dependencies.
        try:
            self.execution.close()
        finally:
            self.closed = True
            self.service.close()


def register_generation(slot: ProviderSlot, generation: CatalogGeneration) -> None:
    """Stage an in-memory recipe; no plugin code loads until slot.switch().

    New PluginService instances see a fresh manifest catalog; earlier service
    snapshots and their execution contexts remain pinned to earlier builds.
    """
    if generation.contract != slot.contract:
        raise ValueError("host functional contract mismatch")

    def start() -> ActiveGeneration:
        service = PluginService.from_manifest_paths(generation.paths)
        execution = None
        try:
            inventory = {(p.id, p.version) for p in service.list_plugins()}
            if (generation.plugin_id, generation.version) not in inventory:
                raise LookupError("requested concrete plugin build not in fresh catalog")
            execution = service.execution(policy=EphemeralPolicy())
            provider = execution.get(f"{generation.plugin_id}@{generation.version}")
            if not callable(getattr(provider, generation.required_method, None)):
                raise TypeError("provider lacks the host-required callable")
            return ActiveGeneration(service=service, execution=execution, provider=provider)
        except BaseException:
            try:
                if execution is not None:
                    execution.close()
            finally:
                service.close()
            raise

    def stop(generation_instance: object) -> None:
        if not isinstance(generation_instance, ActiveGeneration):
            raise TypeError("expected an ActiveGeneration")
        generation_instance.close()

    slot.install(ProviderSpec(
        contract=generation.contract,
        plugin_id=generation.plugin_id,
        revision=generation.version,
        start=start,
        stop=stop,
    ))
