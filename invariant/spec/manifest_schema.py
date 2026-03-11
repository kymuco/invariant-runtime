# invariant/spec/manifest_schema.py
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from packaging.version import InvalidVersion, Version

from invariant.spec.deps import PluginDepsSpec

if TYPE_CHECKING:
    from invariant.spec.meta import PluginMeta
else:
    PluginMeta = Any


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
            
        deps = self.data.get("deps")
        if deps is not None and not isinstance(deps, dict):
            raise ValueError("manifest 'deps' must be an object if present")
        if isinstance(deps, dict):
            mode = str(deps.get("mode") or "none").strip().lower()
            if mode not in ("none", "bundle", "online"):
                raise ValueError("manifest 'deps.mode' must be one of none|bundle|online")
            if mode in ("bundle", "online"):
                req = deps.get("requirements")
                if not isinstance(req, str) or not req.strip():
                    raise ValueError("manifest 'deps.requirements' must be non-empty string")
            if mode == "bundle":
                wd = deps.get("wheels_dir")
                if not isinstance(wd, str) or not wd.strip():
                    raise ValueError("manifest 'deps.wheels_dir' must be non-empty string")

        runtime = self.data.get("runtime")
        if runtime is not None:
            if not isinstance(runtime, dict):
                raise ValueError("manifest 'runtime' must be an object if present")
            config_param = runtime.get("config_param")
            config_prefix = runtime.get("config_prefix")
            if config_param is not None:
                if not isinstance(config_param, str) or not config_param.strip():
                    raise ValueError("manifest 'runtime.config_param' must be non-empty string")
                if not config_param.strip().isidentifier():
                    raise ValueError("manifest 'runtime.config_param' must be a valid identifier")
            if config_prefix is not None:
                if not isinstance(config_prefix, str) or not config_prefix.strip():
                    raise ValueError("manifest 'runtime.config_prefix' must be non-empty string")
            if (config_param is not None or config_prefix is not None) and (
                not isinstance(config_prefix, str) or not config_prefix.strip()
            ):
                raise ValueError("manifest 'runtime.config_prefix' is required when runtime config snapshot is enabled")

    def to_deps(self) -> PluginDepsSpec:
        self.validate()
        return PluginDepsSpec.from_manifest(self.data.get("deps"))

    def to_meta(self) -> PluginMeta:
        self.validate()

        try:
            priority = int(self.data.get("priority", 0))
        except Exception as e:
            raise ValueError("manifest 'priority' must be int-coercible") from e

        # Import lazily to avoid pulling packaging when not needed.
        from invariant.spec.meta import PluginMeta as _PluginMeta
        return _PluginMeta(
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

