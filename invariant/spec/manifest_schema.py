# invariant/spec/manifest_schema.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from packaging.version import InvalidVersion, Version

from invariant.spec.meta import PluginMeta


@dataclass(frozen=True, slots=True)
class PluginManifest:
    data: dict[str, Any]

    def validate(self) -> None:
        plugin_id = self.data.get("id")
        if not isinstance(plugin_id, str) or not plugin_id.strip():
            raise ValueError("manifest must contain non-empty 'id' (str)")

        version = self.data.get("version")
        if version is not None:
            if not isinstance(version, str):
                raise ValueError("manifest 'version' must be a string")
            try:
                Version(version)
            except InvalidVersion as e:
                raise ValueError("bad version format") from e

    def to_meta(self) -> PluginMeta:
        self.validate()

        try:
            priority = int(self.data.get("priority", 0))
        except Exception as e:
            raise ValueError("manifest 'priority' must be int-coercible") from e

        return PluginMeta(
            id=self.data["id"],
            version=self.data.get("version"),
            name=self.data.get("name"),
            description=self.data.get("description"),
            dependencies=self.data.get("dependencies", []),
            categories=self.data.get("categories", []),
            tags=self.data.get("tags", []),
            author=self.data.get("author"),
            maturity=self.data.get("maturity"),
            repo=self.data.get("repo"),
            priority=priority,
            # extra=self.data.get("extra", {}),
        )

