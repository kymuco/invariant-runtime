# invariant/exec/caps/registry.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict
import threading

from invariant.spec.versioning import canon_version_key
from invariant.exec.caps.catalog import CATALOG, OFFICIAL_CAPS
from invariant.exec.caps.ids import cap_base_id, CAP_PREFIX


def split_id_version(raw: str) -> tuple[str, str | None]:
    """
    'x:y@1.2.3' -> ('x:y', '1.2.3')
    'x:y' -> ('x:y', None)
    """
    if "@" not in raw:
        return raw, None
    base, ver = raw.rsplit("@", 1)
    base = base.strip()
    ver = ver.strip() or None
    return base, ver


def is_capability_ref(dep_id: str) -> bool:
    base, _ = split_id_version(dep_id)
    return isinstance(base, str) and base.startswith(CAP_PREFIX)


class CapabilityError(RuntimeError):
    pass


class CapabilityUnavailableError(RuntimeError):
    pass


class CapabilityNotFound(CapabilityError):
    def __init__(self, cap_id: str):
        super().__init__(f"Capability not found: {cap_id}")
        self.cap_id = cap_id


class CapabilityVersionMismatch(CapabilityError):
    def __init__(self, cap_id: str, requested: str | None, provided: str | None):
        super().__init__(
            f"Capability version mismatch for {cap_id}: requested={requested}, provided={provided}"
        )
        self.cap_id = cap_id
        self.requested = requested
        self.provided = provided


@dataclass(frozen=True, slots=True)
class CapabilityEntry:
    value: Any
    version: str | None = None


class CapabilityProvider:
    """
    Execution-scoped capability registry.

    Invariants:
    - capability ids start with 'invariant:capability.'
    - immutable registration (no silent overrides)
    - strict_catalog=True: registration is allowed ONLY for official catalog caps,
      and version is taken ONLY from catalog (no manual versioning).
    """

    def __init__(self, *, strict_versions: bool = True, strict_catalog: bool = True):
        self._strict_versions = bool(strict_versions)
        self._strict_catalog = bool(strict_catalog)
        self._caps: Dict[str, CapabilityEntry] = {}
        self._lock = threading.RLock()
        self._frozen = False

    def freeze(self) -> None:
        """Disallow further registration. Intended after bootstrap."""
        with self._lock:
            self._frozen = True

    def register(self, cap_id: str, value: Any) -> None:
        if not isinstance(cap_id, str) or not cap_id.startswith(CAP_PREFIX):
            raise ValueError(f"Invalid capability id: {cap_id!r}")
        if "@" in cap_id:
            raise ValueError("register() expects base capability id (no @version)")

        base = cap_base_id(cap_id)
        # Hard gate: only official capabilities can be registered in strict_catalog mode.
        spec = CATALOG.get(base) if self._strict_catalog else None
        if self._strict_catalog and base not in OFFICIAL_CAPS:
            raise ValueError(f"Capability not in official catalog: {base}")
        if value is None:
            raise ValueError("Capability value must not be None")
        # In strict_catalog mode: version is ALWAYS the catalog version.
        # In non-strict mode: version is not tracked unless you use register_custom().
        ver_norm: str | None = None
        if spec is not None:
            try:
                ver_norm = canon_version_key(spec.version)
            except ValueError as e:
                raise ValueError(f"Bad catalog capability version for {base}: {spec.version!r}") from e

        with self._lock:
            if self._frozen:
                raise CapabilityError("CapabilityProvider is frozen")
            if cap_id in self._caps:
                raise CapabilityError(f"Capability already registered: {cap_id}")
            self._caps[cap_id] = CapabilityEntry(value=value, version=ver_norm)

    def register_custom(self, cap_id: str, value: Any, *, version: str | None = None) -> None:
        """
        Escape hatch for tests / experiments.
        Only allowed when strict_catalog=False.
        """
        if self._strict_catalog:
            raise CapabilityError("register_custom() is not allowed when strict_catalog=True")
        if not isinstance(cap_id, str) or not cap_id.startswith(CAP_PREFIX):
            raise ValueError(f"Invalid capability id: {cap_id!r}")
        if "@" in cap_id:
            raise ValueError("register_custom() expects base capability id (no @version)")
        if value is None:
            raise ValueError("Capability value must not be None")

        ver_norm = None
        if version is not None:
            try:
                ver_norm = canon_version_key(version)
            except ValueError as e:
                raise ValueError(f"Bad capability version: {version!r}") from e

        with self._lock:
            if self._frozen:
                raise CapabilityError("CapabilityProvider is frozen")
            if cap_id in self._caps:
                raise CapabilityError(f"Capability already registered: {cap_id}")
            self._caps[cap_id] = CapabilityEntry(value=value, version=ver_norm)

    def get(self, cap_ref: str) -> Any:
        """
        cap_ref may include @version:
          'invariant:capability.fs_sandbox@0.1.0'
        """
        base = cap_base_id(cap_ref)
        if self._strict_catalog and base not in OFFICIAL_CAPS:
            raise ValueError(f"Capability not in official catalog: {base}")
        base, requested = split_id_version(cap_ref)
        with self._lock:
            entry = self._caps.get(base)
        if entry is None:
            raise CapabilityNotFound(base)

        if self._strict_versions and requested is not None:
            try:
                req_norm = canon_version_key(requested)
            except ValueError as e:
                raise ValueError(f"Bad requested capability version: {requested!r}") from e
            if entry.version != req_norm:
                raise CapabilityVersionMismatch(base, requested, entry.version)

        return entry.value
