"""Pinned, offline Plugin Catalog v1 for the internal installation planner.

A catalog is inert data: it neither downloads archives nor installs plugins.
A host must independently trust its exact SHA-256 before parsing the bytes.
This is not publisher-signature verification or a marketplace trust service.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Iterable

from invariant.load.installation_plan import (
    ArtifactOffer,
    PluginInstallationPlanner,
)
from invariant.load.plugin_store import LocalPluginStore

_MAX_CATALOG_BYTES = 1024 * 1024
_MAX_ARTIFACTS = 256
_MAX_DEPS_PER_ARTIFACT = 64
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_CATALOG_FIELDS = frozenset({"schema_version", "artifacts"})
_ARTIFACT_FIELDS = frozenset({"ref", "url", "sha256", "dependencies"})


class PluginCatalogError(ValueError):
    """Unverified, malformed, ambiguous, or unsupported plugin catalog."""


def _unique_fields(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise PluginCatalogError(f"duplicate JSON property: {key}")
        result[key] = value
    return result


def _no_nonfinite(value: str):
    raise PluginCatalogError(f"invalid JSON constant: {value}")


@dataclass(frozen=True, slots=True)
class VerifiedPluginCatalog:
    """Immutable, digest-bound set of exact ZIP artifact offers."""

    sha256: str
    offers: tuple[ArtifactOffer, ...]

    @classmethod
    def from_bytes(
        cls, payload: bytes, *, expected_sha256: str,
    ) -> "VerifiedPluginCatalog":
        if not isinstance(payload, bytes) or not 0 < len(payload) <= _MAX_CATALOG_BYTES:
            raise PluginCatalogError("catalog must be 1..1048576 bytes")
        if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
            raise PluginCatalogError("trusted catalog SHA-256 must have 64 hex digits")
        actual = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(actual, expected_sha256.lower()):
            raise PluginCatalogError("catalog SHA-256 mismatch")

        try:
            doc = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=_unique_fields,
                parse_constant=_no_nonfinite,
            )
        except (ValueError, UnicodeError) as exc:
            raise PluginCatalogError(f"invalid catalog JSON: {exc}") from exc
        if not isinstance(doc, dict) or doc.keys() != _CATALOG_FIELDS:
            raise PluginCatalogError("catalog requires only schema_version and artifacts")
        if type(doc["schema_version"]) is not int or doc["schema_version"] != 1:
            raise PluginCatalogError("unsupported catalog schema_version")
        raw = doc["artifacts"]
        if not isinstance(raw, list) or len(raw) > _MAX_ARTIFACTS:
            raise PluginCatalogError("catalog artifacts must be a list of at most 256 entries")

        offers: list[ArtifactOffer] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict) or item.keys() != _ARTIFACT_FIELDS:
                raise PluginCatalogError("artifact requires ref, url, sha256 and dependencies")
            ref, url, sha, deps = (item[key] for key in ("ref", "url", "sha256", "dependencies"))
            if (not isinstance(ref, str) or len(ref) > 256 or
                not isinstance(url, str) or len(url) > 2048 or
                not isinstance(sha, str) or
                not isinstance(deps, list) or len(deps) > _MAX_DEPS_PER_ARTIFACT or
                any(not isinstance(dep, str) or len(dep) > 256 for dep in deps)):
                raise PluginCatalogError("invalid or oversized artifact field")
            try:
                offer = ArtifactOffer(ref=ref, url=url, sha256=sha, dependencies=tuple(deps))
            except ValueError as exc:
                raise PluginCatalogError(f"invalid artifact offer: {exc}") from exc
            if offer.ref in seen:
                raise PluginCatalogError(f"duplicate canonical plugin reference: {offer.ref}")
            if len(offer.dependencies) != len(deps):
                raise PluginCatalogError(f"duplicate required dependency: {offer.ref}")
            seen.add(offer.ref)
            offers.append(offer)
        return cls(actual, tuple(sorted(offers, key=lambda offer: offer.ref)))

    def planner(
        self,
        store: LocalPluginStore,
        *,
        allowed_origins: Iterable[str],
    ) -> PluginInstallationPlanner:
        """Build the existing planner; it still checks host HTTPS policy."""
        return PluginInstallationPlanner(store, self.offers, allowed_origins=allowed_origins)
