# invariant/exec/caps/view.py
from __future__ import annotations

from invariant.exec.caps.registry import CapabilityUnavailableError, split_id_version


class CapabilityView:
    def __init__(
        self,
        base,
        *,
        deny: set[str] | None = None,
        allow: set[str] | None = None,
        deny_prefixes: tuple[str, ...] = (),
        reason: str,
        binder=None,
    ):
        self._base = base
        self._deny = set(deny or set())
        self._allow = set(allow) if allow is not None else None
        self._deny_prefixes = tuple(deny_prefixes)
        self._reason = reason
        self._binder = binder

    def get(self, cap_ref: str):
        base_id, _ = split_id_version(cap_ref)
        for p in self._deny_prefixes:
            if base_id.startswith(p):
                raise CapabilityUnavailableError(f"{base_id}: {self._reason}")

        if self._allow is not None and base_id not in self._allow:
            raise CapabilityUnavailableError(f"{base_id}: {self._reason}")
        if base_id in self._deny:
            raise CapabilityUnavailableError(f"{base_id}: {self._reason}")
        val = self._base.get(cap_ref)
        b = self._binder
        if b is None:
            return val
        return b(base_id, val)
