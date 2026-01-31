# invariant/exec/caps/config_source.py
from __future__ import annotations

import os
from typing import Any, Dict, Iterable


class EnvDictConfigSource:
    """
    Safe config source:
    - primary: provided dict
    - optional: environment, but ONLY when allow_env=True and env_prefix is set
      (prevents accidental secret exfiltration from process env)
    """
    def __init__(
        self,
        data: Dict[str, Any] | None = None,
        *,
        allow_env: bool = False,
        env_prefix: str = "INVARIANT_",
    ):
        self._data = dict(data or {})
        self._allow_env = bool(allow_env)
        self._env_prefix = str(env_prefix or "")
        if self._allow_env and not self._env_prefix:
            raise ValueError("env_prefix must be non-empty when allow_env=True")

    def get(self, key: str) -> Any:
        if key in self._data:
            return self._data[key]
        if self._allow_env:
            env_key = f"{self._env_prefix}{key}"
            if env_key in os.environ:
                return os.environ[env_key]
        raise KeyError(key)

    def list_keys(self, prefix: str = "") -> Iterable[str]:
        # Deliberately does NOT list env keys (avoid leaking env structure).
        for k in sorted(self._data.keys()):
            if not prefix or k.startswith(prefix):
                yield k
                