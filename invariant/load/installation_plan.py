"""Host-approved plan for installing pinned plugins into one LocalPluginStore.

This is an internal orchestrator, not a marketplace, downloader during
plugin discovery, permission-granting service, or dependency version solver.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from invariant.load.acquisition import _validate_request, download_verified_zip
from invariant.load.plugin_store import (
    LocalPluginStore,
    _candidate,
    _catalog_fingerprint,
    _dependencies,
    _reference,
)

_MAX_OFFERS = 256
_MAX_ITEMS = 64
_MAX_TOTAL_BYTES = 256 * 1024 * 1024


class InstallationPlanError(ValueError):
    """Missing/ambiguous dependency, changed plan or missing authorization."""


@dataclass(frozen=True)
class ArtifactOffer:
    """Host-supplied pinned ZIP metadata from an independently trusted source.

    dependencies contains required plugin references only. Capabilities and
    optional plugin dependencies are not installation requirements.
    """

    ref: str
    url: str
    sha256: str
    dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "ref", _reference(self.ref))
        if not isinstance(self.dependencies, (list, tuple)):
            raise InstallationPlanError("offer dependencies must be exact references")
        deps = tuple(sorted({_reference(dep) for dep in self.dependencies}))
        if self.ref in deps:
            raise InstallationPlanError("plugin cannot depend on itself")
        object.__setattr__(self, "dependencies", deps)
        if not isinstance(self.sha256, str) or len(self.sha256) != 64:
            raise InstallationPlanError("offer requires a pinned SHA-256")
        try:
            bytes.fromhex(self.sha256)
        except ValueError as exc:
            raise InstallationPlanError("offer requires hexadecimal SHA-256") from exc
        object.__setattr__(self, "sha256", self.sha256.lower())


@dataclass(frozen=True)
class InstallationPlan:
    roots: tuple[str, ...]
    installed: tuple[str, ...]
    downloads: tuple[ArtifactOffer, ...]  # dependencies before consumers
    catalog_sha256: str
    plan_id: str


class PluginInstallationPlanner:
    """Plan without side effects; install only with approval of exact plan ID.

    The host supplies the entire finite offer graph and allowed HTTPS origins.
    The planner never finds artifact metadata on the network and never grants
    capabilities or authorizes installation on behalf of the user.
    """

    def __init__(
        self,
        store: LocalPluginStore,
        offers: Iterable[ArtifactOffer],
        *,
        allowed_origins: Iterable[str],
    ) -> None:
        self.store = store
        self.allowed_origins = tuple(sorted(set(allowed_origins)))
        offers = tuple(offers)
        if len(offers) > _MAX_OFFERS:
            raise InstallationPlanError("too many offered artifacts")
        by_ref: dict[str, ArtifactOffer] = {}
        for offer in offers:
            if not isinstance(offer, ArtifactOffer):
                raise InstallationPlanError("only ArtifactOffer entries are allowed")
            _validate_request(offer.url, offer.sha256, self.allowed_origins, 20.0, 30 * 1024 * 1024)
            if offer.ref in by_ref and by_ref[offer.ref] != offer:
                raise InstallationPlanError(f"ambiguous offers for {offer.ref}")
            by_ref[offer.ref] = offer
        self.offers = by_ref

    def plan(self, roots: Iterable[str]) -> InstallationPlan:
        if isinstance(roots, str):
            raise InstallationPlanError("roots must be an iterable of exact references")
        roots = tuple(sorted({_reference(root) for root in roots}))
        if not roots or len(roots) > _MAX_ITEMS:
            raise InstallationPlanError("plan requires 1 to 64 exact roots")
        # A single Store lock makes the catalog snapshot and integrity reads
        # consistent. No HTTP or mutable plan publication takes place here.
        with self.store._lock:
            state = self.store._read_state()
            manifests = self.store._manifests(state["plugins"])
            catalog_sha256 = _catalog_fingerprint(state)
            existing_sha = {
                ref: json.loads((self.store.objects / digest / ".invariant-receipt.json").read_text(encoding="utf-8"))["archive_sha256"]
                for ref, digest in state["plugins"].items()
            }

        visited: set[str] = set()
        visiting: set[str] = set()
        installed: list[str] = []
        downloads: list[ArtifactOffer] = []

        def visit(ref: str) -> None:
            if ref in visiting:
                raise InstallationPlanError(f"dependency cycle: {ref}")
            if ref in visited:
                return
            visiting.add(ref)
            offer = self.offers.get(ref)
            if ref in manifests:
                dependencies = tuple(sorted(set(_dependencies(manifests[ref]))))
                if offer is not None and (existing_sha[ref] != offer.sha256 or dependencies != offer.dependencies):
                    raise InstallationPlanError(f"installed artifact conflicts with approved offer: {ref}")
            elif offer is not None:
                dependencies = offer.dependencies
            else:
                raise InstallationPlanError(f"missing required pinned artifact offer: {ref}")
            for dependency in dependencies:
                visit(dependency)
            visiting.remove(ref)
            visited.add(ref)
            if ref in manifests:
                installed.append(ref)
            else:
                assert offer is not None
                downloads.append(offer)
            if len(visited) > _MAX_ITEMS:
                raise InstallationPlanError("installation plan exceeds 64 plugins")

        for root in roots:
            visit(root)

        snapshot = {
            "roots": roots,
            "installed": tuple(installed),
            "downloads": tuple((o.ref, o.url, o.sha256, o.dependencies) for o in downloads),
            "catalog_sha256": catalog_sha256,
            "allowed_origins": self.allowed_origins,
        }
        plan_id = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return InstallationPlan(roots, tuple(installed), tuple(downloads), catalog_sha256, plan_id)

    def apply(self, plan: InstallationPlan, *, approved_plan_id: str) -> dict:
        if not isinstance(plan, InstallationPlan) or plan.plan_id != approved_plan_id:
            raise InstallationPlanError("explicit host approval of this exact plan is required")
        if self.plan(plan.roots) != plan:
            raise InstallationPlanError("plan/store/offer state changed; new approval required")
        # Download *all* missing ZIPs before a single catalog transaction. On
        # download or manifest mismatch, no Store.install call has happened.
        with tempfile.TemporaryDirectory(prefix="invariant-plugin-install-plan-") as folder:
            archives: list[Path] = []
            hashes: dict[str, str] = {}
            total = 0
            for idx, offer in enumerate(plan.downloads):
                with download_verified_zip(
                    offer.url,
                    expected_sha256=offer.sha256,
                    allowed_origins=self.allowed_origins,
                ) as downloaded:
                    total += downloaded.stat().st_size
                    if total > _MAX_TOTAL_BYTES:
                        raise InstallationPlanError("aggregate plan downloads exceed 256 MiB")
                    # Store.install expects SHA mappings keyed by unique file
                    # *names*, whereas HTTPS downloader returns plugin.zip.
                    target = Path(folder) / f"{idx:03d}.zip"
                    shutil.copyfile(downloaded, target)
                candidate = _candidate(target, offer.sha256)
                if candidate.ref != offer.ref:
                    raise InstallationPlanError(f"downloaded artifact identity differs: {offer.ref}")
                if tuple(sorted(set(_dependencies(candidate.manifest)))) != offer.dependencies:
                    raise InstallationPlanError(f"downloaded dependencies differ from approved offer: {offer.ref}")
                archives.append(target)
                hashes[target.name] = offer.sha256
            return self.store.install(
                archives, roots=plan.roots, expected_sha256=hashes,
                expected_catalog_sha256=plan.catalog_sha256,
            )
