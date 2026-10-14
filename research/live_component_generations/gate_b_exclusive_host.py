"""Gate B.2: actual Invariant plugin stop-first routing, research only.

A host-provided resource-free predicate is the only authority to permit
exclusive candidate startup. The prior instance must fully drain/stop
before the new actual ExecutionContext is constructed. The existing
StopFirstSlot is the SINGLE route/catalog visibility owner.

This is not a GPU release monitor: tests use a cross-instance filesystem
exclusion token. In production, real host-owned resource evidence is needed.
Not a public PluginService API, not a multi-scope runtime, not code reload.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Callable, Iterable
from uuid import uuid4

from catalog_epoch import CatalogConflict, CatalogEpochs, StalePlan
from gate_a_catalog_owned_host import discover_candidates, research_manifest_loader
from gate_a_catalog_view import CatalogView
from gate_a_pinned_execution import open_pinned_execution
from gate_a_readiness_host import PublishedRoute
from gate_c_asset_fence import AssetFence, AssetSeal
from gate_c_snapshot_vault import Snapshot as AssetSnapshot, SnapshotVault
from gate_c_locked_assets import LockedBundle, NoCopyModelGuard
from prototype import ProviderSpec, ProviderUnavailable
from stop_first import Phase, Snapshot, StopFirstSlot


@dataclass(frozen=True, slots=True)
class ExclusiveStage:
    owner: object
    base_identity: tuple[str, str]
    view: CatalogView
    root_key: str
    signature: tuple[tuple[str, str, tuple[str, ...]], ...]
    ticket: str
    asset_seal: AssetSeal | None = None
    asset_snapshot: AssetSnapshot | None = None
    locked_bundle: LockedBundle | None = None


class RealStopFirstHost:
    """One named root, mandatory EXECUTION dependencies, synchronous method.

    The exclusive slot holds the only effective route. Stage provides
    validated *metadata* but does not prepare (and cannot allocate) the
    candidate while the old plugin still owns the exclusive resource.
    """

    def __init__(
        self,
        roots: Iterable[Path],
        *,
        root_ref: str,
        is_resource_free: Callable[[], bool],
        method: str = "synthesize",
        model_asset_paths: Callable[[str], Iterable[Path]] | None = None,
        snapshot_config_prefix: str | None = None,
        snapshot_max_file_bytes: int = 16 * 1024 * 1024,
        locked_model_config_prefix: str | None = None,
    ):
        if not root_ref or "@" in root_ref:
            raise ValueError("root_ref must be an unversioned plugin identifier")
        if not callable(is_resource_free):
            raise TypeError("host-owned resource probe is required")
        if not isinstance(method, str) or not method.isidentifier():
            raise ValueError("invalid method name")
        self.roots = tuple(Path(p).resolve() for p in roots)
        if not self.roots:
            raise ValueError("roots required")
        self.loader = research_manifest_loader(self.roots)
        self.tracer = None
        self.root_ref = root_ref
        self.method = method
        self._asset_fence = (
            AssetFence(model_asset_paths) if model_asset_paths is not None else None
        )
        if snapshot_config_prefix is not None:
            if self._asset_fence is None:
                raise ValueError("snapshot injection requires model_asset_paths")
            if not snapshot_config_prefix or not snapshot_config_prefix.endswith("."):
                raise ValueError("snapshot_config_prefix must end with '.'")
        if locked_model_config_prefix is not None:
            if self._asset_fence is None or snapshot_config_prefix is not None:
                raise ValueError(
                    "C.4 requires declared model assets and cannot combine with C.3 copying"
                )
            if not locked_model_config_prefix.endswith("."):
                raise ValueError("locked_model_config_prefix must end with '.'")
        self._locked_prefix = locked_model_config_prefix
        self._locked_guard = (
            NoCopyModelGuard(self._asset_fence)
            if locked_model_config_prefix is not None else None
        )
        self._locked_bundles: dict[str, LockedBundle] = {}
        self._snapshot_prefix = snapshot_config_prefix
        self._snapshot_vault = (
            SnapshotVault(max_file_bytes=snapshot_max_file_bytes)
            if snapshot_config_prefix is not None else None
        )
        self._management = RLock()
        self._installed: set[str] = set()
        self._slot = StopFirstSlot(root_ref, is_resource_free=is_resource_free)
        initial = CatalogView(1, discover_candidates(self.roots, self.loader))
        root = initial.resolve_graph(root_ref).root
        try:
            identity = self._install(initial, root, "initial")
            self._slot.replace(*identity)
        except BaseException:
            self._close_model_bundles()
            if self._snapshot_vault is not None:
                self._snapshot_vault.close()
            raise

    @staticmethod
    def _signature(candidates):
        found = CatalogEpochs._normalize(candidates)
        return CatalogEpochs._signature(found)

    def _install(
        self, view: CatalogView, root_key: str, ticket: str,
        *, asset_seal: AssetSeal | None = None,
        asset_snapshot: AssetSnapshot | None = None,
        locked_bundle: LockedBundle | None = None,
    ):
        # A selected candidate's asset seal belongs to the generation
        # starter. A rollback re-verifies the original version's seal.
        if self._asset_fence is not None and asset_seal is None:
            asset_seal = self._asset_fence.pin(root_key)
        if self._snapshot_vault is not None:
            self._ensure_snapshot_contract(view, root_key)
            if asset_seal is None:
                raise StalePlan("snapshot requires a model asset seal")
            if asset_snapshot is None:
                asset_snapshot = self._snapshot_vault.create(asset_seal)
            self._snapshot_vault.verify(asset_snapshot)
        if self._locked_guard is not None:
            self._ensure_config_contract(view, root_key, self._locked_prefix)
            if asset_seal is None:
                raise StalePlan("C.4 requires the complete sealed asset list")
            if locked_bundle is None:
                locked_bundle = self._locked_guard.acquire(asset_seal)
            else:
                self._locked_guard.verify(locked_bundle)
        try:
            spec = ProviderSpec(
                self.root_ref,
                self.root_ref,
                ticket,
                lambda: self._start(
                    view, root_key, asset_seal, asset_snapshot, locked_bundle
                ),
                self._stop,
            )
            self._slot.install(spec)
            self._installed.add(ticket)
            if locked_bundle is not None:
                self._locked_bundles[ticket] = locked_bundle
            return spec.identity
        except BaseException:
            if locked_bundle is not None and ticket not in self._locked_bundles:
                locked_bundle.close()
            raise

    @staticmethod
    def _ensure_config_contract(
        view: CatalogView, root_key: str, prefix: str
    ) -> None:
        # The plugin explicitly opts in to existing Invariant config injection.
        descriptor = view.resolve(root_key)
        extra = descriptor.source.extra or {}
        if (
            extra.get("config_prefix") != prefix
            or extra.get("config_param") != "config"
        ):
            raise CatalogConflict(
                "candidate must explicitly accept model artifact paths via "
                "runtime.config_prefix and runtime.config_param=config"
            )

    def _ensure_snapshot_contract(self, view: CatalogView, root_key: str) -> None:
        self._ensure_config_contract(view, root_key, self._snapshot_prefix)

    def _close_model_bundles(self, *, keep: str | None = None) -> None:
        # Metadata handles do not own the routing decision. Never release
        # handles used by an ACTIVE or potentially FAILED generation.
        for ticket, bundle in tuple(self._locked_bundles.items()):
            if ticket != keep:
                bundle.close()
                del self._locked_bundles[ticket]

    def _start(
        self, view: CatalogView, root_key: str,
        asset_seal: AssetSeal | None = None,
        asset_snapshot: AssetSnapshot | None = None,
        locked_bundle: LockedBundle | None = None,
    ):
        # Called ONLY after stop-first has verified is_resource_free().
        # Revalidate just before opening the real ExecutionContext; the
        # stage/preflight checks alone cannot bind model asset bytes.
        if self._asset_fence is not None:
            if asset_seal is None:
                raise StalePlan("exclusive candidate missing model asset seal")
            self._asset_fence.verify(asset_seal)
        config = None
        if self._snapshot_vault is not None:
            if asset_snapshot is None or asset_snapshot.root_key != root_key:
                raise StalePlan("model snapshot missing or bound to wrong version")
            self._snapshot_vault.verify(asset_snapshot)
            config = {self._snapshot_prefix + "model_path": str(asset_snapshot.file)}
        if self._locked_guard is not None:
            if locked_bundle is None or locked_bundle.seal.root_key != root_key:
                raise StalePlan("C.4 candidate model handle lease is missing")
            self._locked_guard.verify(locked_bundle)
            config = locked_bundle.config(self._locked_prefix)
        context = open_pinned_execution(self, view, config=config)
        try:
            plugin = context.get(root_key)
            if not callable(getattr(plugin, self.method, None)):
                raise CatalogConflict("required method missing after initialization")
        except BaseException:
            context.close()
            raise
        return PublishedRoute(view, context, plugin, root_key)

    @staticmethod
    def _stop(route: PublishedRoute):
        # Invariant Runtime may catch shutdown exceptions internally, so the
        # host NEVER equates a successful close return with release evidence.
        route.context.close()

    def status(self) -> Snapshot:
        return self._slot.snapshot()

    def snapshot(self) -> CatalogView:
        with self._slot.acquire() as route:
            return route.view

    def call(self, *args, **kwargs):
        with self._slot.acquire() as route:
            return getattr(route.plugin, self.method)(*args, **kwargs)

    def stage(self) -> ExclusiveStage:
        # Snapshot is protected by one admission lease. During an exclusive
        # stop-first transition, admission fails (no falsely stable routing).
        current = self._slot.snapshot()
        if current.phase is not Phase.ACTIVE or current.active is None:
            raise ProviderUnavailable("exclusive provider is unavailable")
        with self._slot.acquire() as old:
            base = old.view
        found = CatalogEpochs._normalize(discover_candidates(self.roots, self.loader))
        for key, prior in base.entries.items():
            new = found.get(key)
            if new is None:
                raise CatalogConflict(f"silent removal forbidden: {key}")
            if (new.fingerprint, new.dependencies) != (prior.fingerprint, prior.dependencies):
                raise CatalogConflict(f"same-version mutation forbidden: {key}")
        added = set(found) - set(base.entries)
        candidate = CatalogView(base.epoch + 1, found.values())
        root_key = candidate.resolve_graph(self.root_ref).root
        if root_key not in added:
            raise CatalogConflict("exclusive switch requires new selected root version")
        asset_seal = (
            self._asset_fence.pin(root_key) if self._asset_fence is not None else None
        )
        asset_snapshot = None
        if self._snapshot_vault is not None:
            self._ensure_snapshot_contract(candidate, root_key)
            if asset_seal is None:
                raise StalePlan("snapshot requires declared model assets")
            asset_snapshot = self._snapshot_vault.create(asset_seal)
        locked_bundle = None
        ticket = uuid4().hex
        if self._locked_guard is not None:
            self._ensure_config_contract(candidate, root_key, self._locked_prefix)
            if asset_seal is None:
                raise StalePlan("C.4 requires a sealed model bundle")
            if len(self._locked_bundles) >= 5:
                raise StalePlan("C.4 maximum pending model file leases reached")
            locked_bundle = self._locked_guard.acquire(asset_seal)
            self._locked_bundles[ticket] = locked_bundle
        return ExclusiveStage(
            owner=self,
            base_identity=current.active,
            view=candidate,
            root_key=root_key,
            signature=CatalogEpochs._signature(found),
            ticket=ticket,
            asset_seal=asset_seal,
            asset_snapshot=asset_snapshot,
            locked_bundle=locked_bundle,
        )

    def replace(
        self,
        staged: ExclusiveStage,
        *,
        drain_timeout_s: float = 1.0,
        release_timeout_s: float = 1.0,
    ):
        """Metadata preflight -> drain -> stop -> prove free -> start -> route.

        Returns the newly published CatalogView, or raises with the slot
        phase reflecting safe recovery/failure. Candidate start never occurs
        when release evidence is absent. FAILED requires external recovery.
        """
        if staged.owner is not self:
            raise CatalogConflict("stage belongs to a different host")
        if drain_timeout_s < 0 or release_timeout_s < 0:
            raise ValueError("timeouts must be nonnegative")
        with self._management:
            # Validate before fencing admission or tearing down old plugin.
            if self._signature(discover_candidates(self.roots, self.loader)) != staged.signature:
                raise StalePlan("source changed after exclusive stage")
            if self._asset_fence is not None:
                if staged.asset_seal is None:
                    raise StalePlan("exclusive candidate missing model asset seal")
                self._asset_fence.verify(staged.asset_seal)
            if self._snapshot_vault is not None:
                if staged.asset_snapshot is None:
                    raise StalePlan("candidate snapshot missing")
                self._snapshot_vault.verify(staged.asset_snapshot)
            if self._locked_guard is not None:
                if staged.locked_bundle is None:
                    raise StalePlan("C.4 locked staged model bundle missing")
                if self._locked_bundles.get(staged.ticket) is not staged.locked_bundle:
                    raise StalePlan("C.4 staged bundle no longer owned by this host")
                self._locked_guard.verify(staged.locked_bundle)
            status = self._slot.snapshot()
            if self._slot.is_closed:
                raise RuntimeError("exclusive slot is closed")
            if status.phase is Phase.FAILED:
                raise RuntimeError("exclusive slot FAILED; manual recovery required")
            if status.phase is not Phase.ACTIVE or status.active != staged.base_identity:
                raise StalePlan("active exclusive route changed since staging")
            if staged.ticket not in self._installed:
                self._install(
                    staged.view, staged.root_key, staged.ticket,
                    asset_seal=staged.asset_seal,
                    asset_snapshot=staged.asset_snapshot,
                    locked_bundle=staged.locked_bundle,
                )
            self._slot.replace(
                self.root_ref,
                staged.ticket,
                drain_timeout_s=drain_timeout_s,
                release_timeout_s=release_timeout_s,
            )
            if self._locked_guard is not None:
                # Switch completed; old process is stopped and admitted
                # leases drained, so discard only retired file handles.
                self._close_model_bundles(keep=staged.ticket)
            return self.snapshot()

    def close(self, *, drain_timeout_s=1.0, release_timeout_s=1.0):
        with self._management:
            self._slot.close(
                drain_timeout_s=drain_timeout_s,
                release_timeout_s=release_timeout_s,
            )
            # Only dispose on fully successful terminal resource release.
            if self._slot.is_closed and self._snapshot_vault is not None:
                self._snapshot_vault.close()
            if self._slot.is_closed and self._locked_guard is not None:
                self._close_model_bundles()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
