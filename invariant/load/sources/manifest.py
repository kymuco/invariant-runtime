# invariant/load/sources/manifest.py
from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from invariant.load.env.env_manager import EnvManager
from invariant.load.sources.base import PluginSource
from invariant.spec.deps import PluginDepsSpec
from invariant.spec.descriptor import PluginDescriptor
from invariant.spec.errors import PluginLoadError
from invariant.spec.plugin import BasePlugin
from invariant.spec.source import PluginSourceSpec

if TYPE_CHECKING:
    from invariant.spec.meta import PluginMeta

logger = logging.getLogger(__name__)

SUPPORTED_MANIFEST_VERSIONS = {1}
MAX_MANIFEST_BYTES = 1_000_000


class ManifestSource(PluginSource):
    """
    Manifest-based plugin source (manifest_version=1 only).

    Контракт:
    - manifest полностью описывает meta + source
    - discover не делает рефлексию/подгрузку кода, кроме опционального base_class фильтра
    - module_name вычисляется стабильно из plugin id + version
    """

    kind: Literal["manifest"] = "manifest"

    def __init__(self, search_paths: list[Path | str], *, env_manager: EnvManager | None = None):
        self.search_paths = [Path(p).resolve() for p in search_paths]
        self.env_manager = env_manager

    # ------------------------------------------------------------------
    # DISCOVER
    # ------------------------------------------------------------------

    def discover(
        self,
        base_class: type[object] | None = None,
        include_unstable: bool = False,
        *,
        dev_mode: bool = False,
        fail_fast: bool = False,
        **kwargs,
    ) -> list[PluginDescriptor]:
        descriptors: list[PluginDescriptor] = []

        for base in self.search_paths:
            if not base.exists():
                logger.warning("ManifestSource path does not exist: %s", base)
                continue

            for manifest_path in base.rglob("manifest.json"):
                try:
                    descriptor = self._read_manifest(manifest_path, dev_mode=dev_mode)

                    if not include_unstable and descriptor.meta.maturity != "stable":
                        logger.info("Skipping unstable plugin: %s", descriptor.meta.id)
                        continue

                    # NOTE: base_class фильтр требует загрузки модуля, иначе не проверить issubclass.
                    # Если хочешь 100% no-reflection на discover — убери этот блок и фильтруй позже.
                    if base_class is not None:
                        deps_raw = (descriptor.source.extra or {}).get("deps") if descriptor.source.extra else None
                        deps = PluginDepsSpec.from_manifest(deps_raw)
                        if deps.is_isolated():
                            logger.info(
                                "Skipping plugin %s during base_class filtering: isolated deps (%s) cannot be imported safely in discover()",
                                descriptor.meta.id,
                                deps.mode,
                            )
                            continue
                        mod = descriptor.source.module
                        path = descriptor.source.path

                        if mod is None or path is None:
                            raise ValueError("manifest source requires both 'module' and 'path'")

                        module = self._load_module(mod, path)
                        name = descriptor.source.class_name
                        if not name:
                            raise ValueError("manifest source requires class_name")

                        cls = getattr(module, name, None)
                        if not isinstance(cls, type) or not issubclass(cls, base_class):
                            logger.info(
                                "Skipping plugin %s: does not match base_class",
                                descriptor.meta.id,
                            )
                            continue

                    descriptors.append(descriptor)

                except PluginLoadError as e:
                    logger.error(str(e))
                    if fail_fast:
                        raise

        return descriptors

    # ------------------------------------------------------------------
    # READ / PARSE
    # ------------------------------------------------------------------

    def _runtime_block_v1(self, data: dict[str, Any], path: Path) -> dict[str, Any]:
        runtime = data.get("runtime")
        if runtime is None:
            return {}
        if not isinstance(runtime, dict):
            raise PluginLoadError(f"'runtime' must be an object in {path}")
        return runtime

    def _parse_timeouts_v1(self, data: dict[str, Any], path: Path) -> tuple[float | None, dict[str, float | None]]:
        """
        Parse optional runtime timeouts from manifest.

        Supported (either):
          - top-level:
              call_timeout_s: number|null
              method_timeouts: { method: number|null, ... }
          - nested:
              runtime: { call_timeout_s, method_timeouts }
        """
        runtime = self._runtime_block_v1(data, path)

        # Nested wins over top-level if both provided
        call_timeout_raw = runtime.get("call_timeout_s", data.get("call_timeout_s"))
        method_timeouts_raw = runtime.get("method_timeouts", data.get("method_timeouts"))

        call_timeout_s: float | None
        if call_timeout_raw is None:
            call_timeout_s = None
        else:
            if not isinstance(call_timeout_raw, (int, float)):
                raise PluginLoadError(f"'call_timeout_s' must be number or null in {path}")
            call_timeout_s = float(call_timeout_raw)
            if call_timeout_s <= 0:
                raise PluginLoadError(f"'call_timeout_s' must be > 0 or null in {path}")

        method_timeouts: dict[str, float | None] = {}
        if method_timeouts_raw is None:
            return call_timeout_s, method_timeouts
        if not isinstance(method_timeouts_raw, dict):
            raise PluginLoadError(f"'method_timeouts' must be an object or null in {path}")

        for k, v in method_timeouts_raw.items():
            if not isinstance(k, str) or not k.strip():
                raise PluginLoadError(f"'method_timeouts' keys must be non-empty strings in {path}")
            if v is None:
                method_timeouts[k] = None
                continue
            if not isinstance(v, (int, float)):
                raise PluginLoadError(
                    f"'method_timeouts.{k}' must be number or null in {path}"
                )
            fv = float(v)
            if fv <= 0:
                raise PluginLoadError(
                    f"'method_timeouts.{k}' must be > 0 or null in {path}"
                )
            method_timeouts[k] = fv
        return call_timeout_s, method_timeouts

    def _parse_runtime_config_v1(self, data: dict[str, Any], path: Path) -> tuple[str | None, str | None]:
        runtime = self._runtime_block_v1(data, path)
        config_param_raw = runtime.get("config_param")
        config_prefix_raw = runtime.get("config_prefix")

        if config_param_raw is None and config_prefix_raw is None:
            return None, None

        if config_param_raw is None:
            config_param = "config"
        else:
            if not isinstance(config_param_raw, str) or not config_param_raw.strip():
                raise PluginLoadError(f"'runtime.config_param' must be a non-empty string in {path}")
            config_param = config_param_raw.strip()
            if not config_param.isidentifier():
                raise PluginLoadError(f"'runtime.config_param' must be a valid identifier in {path}")

        if not isinstance(config_prefix_raw, str) or not config_prefix_raw.strip():
            raise PluginLoadError(f"'runtime.config_prefix' must be a non-empty string in {path}")
        config_prefix = config_prefix_raw.strip()
        return config_param, config_prefix

    def _read_manifest(self, manifest_path: Path, *, dev_mode: bool) -> PluginDescriptor:
        data = self._read_json(manifest_path)
        self._validate_manifest_v1(data, manifest_path)

        source_block = data["source"]
        deps_raw = data.get("deps")
        deps = PluginDepsSpec.from_manifest(deps_raw)

        meta = self._parse_meta_v1(data, manifest_path)

        if (not dev_mode) and meta.version is None:
            raise PluginLoadError(
                f"Manifest plugin {meta.id} must declare version in strict mode: {manifest_path}",
                meta_id=meta.id,
            )

        plugin_root = manifest_path.parent.resolve()
        code_rel = str(source_block["code_path"])
        code_path = (plugin_root / code_rel).resolve()
        if not code_path.exists():
            raise PluginLoadError(f"Plugin code file not found: {code_path}")
        self._ensure_under_root(plugin_root, code_path, what="source.code_path", meta_id=meta.id)

        module_name = self._build_module_name(manifest_path, meta)

        call_timeout_s, method_timeouts = self._parse_timeouts_v1(data, manifest_path)
        config_param, config_prefix = self._parse_runtime_config_v1(data, manifest_path)

        deps_extra: dict[str, Any] | None = None
        if deps_raw is not None:
            # normalize to canonical json-safe dict
            deps_extra = {
                "mode": deps.mode,
                "requirements": deps.requirements,
                "wheels_dir": deps.wheels_dir,
                "index_url": deps.index_url,
                "extra_index_urls": list(deps.extra_index_urls),
                "trusted_hosts": list(deps.trusted_hosts),
                "only_binary": deps.only_binary,
            }

        extra: dict[str, Any] = {
            "manifest_path": str(manifest_path),
            "manifest_version": 1,
            "plugin_root": str(plugin_root),
            "code_rel": code_rel,
        }
        
        if deps_extra is not None:
            extra["deps"] = deps_extra
        if call_timeout_s is not None:
            extra["call_timeout_s"] = call_timeout_s
        if method_timeouts:
            extra["method_timeouts"] = method_timeouts
        if config_param is not None:
            extra["config_param"] = config_param
            extra["config_prefix"] = config_prefix

        source = PluginSourceSpec(
            kind=self.kind,
            path=code_path,
            module=module_name,
            class_name=source_block["class_name"],
            extra=extra,
        )

        return PluginDescriptor(meta=meta, source=source)
    
    def _ensure_under_root(self, root: Path, p: Path, *, what: str, meta_id: str) -> None:
        """
        Prevent path traversal (../) escapes from plugin_root.
        """
        try:
            p.resolve().relative_to(root.resolve())
        except ValueError:
            raise PluginLoadError(f"{what} must be under plugin_root: {p}", meta_id=meta_id) from None

    def _read_json(self, manifest_path: Path) -> dict[str, Any]:
        try:
            st = manifest_path.stat()
            if st.st_size > MAX_MANIFEST_BYTES:
                raise PluginLoadError(f"Manifest too large (>1MB): {manifest_path}")
            raw = manifest_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except PluginLoadError:
            raise
        except Exception as e:
            raise PluginLoadError(f"Failed to read manifest: {manifest_path}", cause=e) from e

        if not isinstance(data, dict):
            raise PluginLoadError(f"Manifest root must be an object: {manifest_path}")

        return data

    def _parse_meta_v1(self, data: dict[str, Any], manifest_path: Path) -> PluginMeta:
        from invariant.spec.meta import PluginMeta as _PluginMeta

        # Copy only fields that belong to PluginMeta (remove non-meta keys)
        meta_dict = dict(data)
        meta_dict.pop("manifest_version", None)
        meta_dict.pop("source", None)
        meta_dict.pop("deps", None)
        # runtime config (not PluginMeta fields)
        meta_dict.pop("runtime", None)
        meta_dict.pop("call_timeout_s", None)
        meta_dict.pop("method_timeouts", None)

        # Normalize scalar optional strings
        for k in ("name", "description", "author", "repo"):
            if k in meta_dict and meta_dict[k] is not None and not isinstance(meta_dict[k], str):
                raise PluginLoadError(f"'{k}' must be string or null in {manifest_path}")

        # Normalize collections into tuples
        meta_dict["categories"] = tuple(meta_dict.get("categories") or ())
        meta_dict["tags"] = tuple(meta_dict.get("tags") or ())

        # Dependencies (v1 only)
        # raw_deps = meta_dict.get("dependencies") or []
        # if not isinstance(raw_deps, list):
        #     raise PluginLoadError(f"'dependencies' must be list in {manifest_path}")
        # deps = tuple(self._parse_dep_object(dep, manifest_path) for dep in raw_deps)
        # meta_dict["dependencies"] = deps

        try:
            meta = _PluginMeta(**meta_dict)
        except TypeError as e:
            raise PluginLoadError(f"Invalid PluginMeta in manifest: {manifest_path}", cause=e) from e

        if not meta.id:
            raise PluginLoadError(f"Manifest plugin must define meta.id: {manifest_path}")

        return meta

    # def _parse_dep_object(self, dep: Any, manifest_path: Path) -> DependencySpec:
    #     if not isinstance(dep, dict):
    #         raise PluginLoadError(f"Each dependency must be an object in {manifest_path}")

    #     dep_id = dep.get("id")
    #     param = dep.get("param")
    #     scope_raw = dep.get("scope", "EXECUTION")
    #     optional_raw = dep.get("optional", False)

    #     if not dep_id or not isinstance(dep_id, str):
    #         raise PluginLoadError(f"Dependency missing valid 'id' in {manifest_path}")

    #     if not param or not isinstance(param, str):
    #         raise PluginLoadError(
    #             f"Dependency '{dep_id}' missing valid 'param' (required) in {manifest_path}"
    #         )

    #     if not isinstance(scope_raw, str):
    #         raise PluginLoadError(
    #             f"Dependency '{dep_id}' has invalid 'scope' type (must be string) in {manifest_path}"
    #         )

    #     try:
    #         scope = DependencyScope[scope_raw.strip().upper()]
    #     except KeyError:
    #         allowed = ", ".join([s.name for s in DependencyScope])
    #         raise PluginLoadError(
    #             f"Dependency '{dep_id}' has invalid scope='{scope_raw}' in {manifest_path}. "
    #             f"Allowed: {allowed}"
    #         )

    #     if not isinstance(optional_raw, bool):
    #         raise PluginLoadError(
    #             f"Dependency '{dep_id}' has invalid 'optional' (must be boolean) in {manifest_path}"
    #         )

    #     return DependencySpec(
    #         id=dep_id,
    #         param=param,
    #         scope=scope,
    #         optional=optional_raw,
    #     )

    # ------------------------------------------------------------------
    # LOAD
    # ------------------------------------------------------------------

    def can_load(self, source: PluginSourceSpec) -> bool:
        return source.kind == self.kind

    def load(
        self,
        source: PluginSourceSpec,
        registry=None,
        *,
        descriptor: PluginDescriptor | None = None,
        **kwargs
    ) -> BasePlugin:
        if not source.module or not source.class_name or not source.path:
            raise PluginLoadError(f"Incomplete manifest source spec: {source}")
        
        # ---- isolated deps path (separate venv + process) ----
        if descriptor is not None:
            deps_raw = (descriptor.source.extra or {}).get("deps") if descriptor.source.extra else None
            deps = PluginDepsSpec.from_manifest(deps_raw)
            if deps.is_isolated():
                if self.env_manager is None:
                    raise PluginLoadError(
                        "ManifestSource.env_manager is required for isolated plugins",
                        meta_id=descriptor.meta.id,
                    )
                from invariant.load.isolated_proxy import IsolatedPluginProxy
                return IsolatedPluginProxy(descriptor=descriptor, env_manager=self.env_manager)

        module = self._load_module(source.module, source.path)

        cls = getattr(module, source.class_name, None)
        if cls is None:
            raise PluginLoadError(
                f"Plugin class '{source.class_name}' not found in {source.module}"
            )

        if not issubclass(cls, BasePlugin):
            raise PluginLoadError(f"Class {source.class_name} is not a BasePlugin")

        try:
            if descriptor is not None:
                try:
                    return cls(meta=descriptor.meta, context=None)
                except TypeError:
                    try:
                        return cls(meta=descriptor.meta)
                    except TypeError:
                        return cls()

            return cls()
        except Exception as e:
            raise PluginLoadError(
                f"Plugin {source.class_name} constructor failed",
                cause=e,
            ) from e


    # ------------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------------

    def _build_module_name(self, manifest_path: Path, meta: PluginMeta) -> str:
        """
        Stable synthetic module name for a manifest plugin.
        Must not depend on package layout and must avoid collisions.
        """
        h = hashlib.sha256(str(manifest_path.resolve()).encode("utf-8")).hexdigest()[:16]
        safe_id = meta.id.replace(":", "_").replace("-", "_").lower()
        return f"invariant_manifest_{safe_id}_{h}"


    def _load_module(self, module_name: str, file: Path):
        if module_name in sys.modules:
            return sys.modules[module_name]

        spec = importlib.util.spec_from_file_location(module_name, file)
        if spec is None or spec.loader is None:
            raise PluginLoadError(f"Cannot create module spec for {file}")

        module = importlib.util.module_from_spec(spec)

        try:
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception as e:
            sys.modules.pop(module_name, None)
            logger.exception("Failed to load module %s from %s: %s", module_name, file, e)
            raise PluginLoadError(f"Failed to load module {module_name} from {file}", cause=e) from e

        return module

    def _validate_manifest_v1(self, data: dict[str, Any], path: Path) -> None:
        version = data.get("manifest_version")
        if version not in SUPPORTED_MANIFEST_VERSIONS:
            raise PluginLoadError(
                f"Unsupported manifest_version={version} in {path} (expected 1)"
            )

        manifest_id = data.get("id")
        if not isinstance(manifest_id, str) or not manifest_id.strip():
            raise PluginLoadError(f"Manifest missing valid 'id': {path}")

        if "source" not in data or not isinstance(data["source"], dict):
            raise PluginLoadError(f"Manifest missing 'source' block: {path}")

        source = data["source"]

        code_path = source.get("code_path")
        class_name = source.get("class_name")

        if not isinstance(code_path, str) or not code_path.strip():
            raise PluginLoadError(f"Manifest source missing valid code_path: {path}")

        if not isinstance(class_name, str) or not class_name.strip():
            raise PluginLoadError(f"Manifest source missing valid class_name: {path}")
        
        # Optional runtime timeouts block
        # (validation here = fast fail, actual extraction happens in _read_manifest)
        try:
            self._parse_timeouts_v1(data, path)
        except PluginLoadError:
            raise
        except Exception as e:
            raise PluginLoadError(f"Invalid runtime timeouts in {path}: {e}") from e
        try:
            self._parse_runtime_config_v1(data, path)
        except PluginLoadError:
            raise
        except Exception as e:
            raise PluginLoadError(f"Invalid runtime config snapshot metadata in {path}: {e}") from e

        deps = data.get("dependencies", [])
        if deps is None:
            deps = []
        if not isinstance(deps, list):
            raise PluginLoadError(f"'dependencies' must be list in {path}")
        for i, dep in enumerate(deps):
            if not isinstance(dep, dict):
                raise PluginLoadError(f"dependencies[{i}] must be object in {path}")

        # Python deps block (isolation)
        pydeps = data.get("deps")
        if pydeps is None:
            return  # no deps block -> ok
        if not isinstance(pydeps, dict):
            raise PluginLoadError(f"'deps' must be an object in {path}")
        try:
            spec = PluginDepsSpec.from_manifest(pydeps)
        except Exception as e:
            raise PluginLoadError(f"Invalid deps block in {path}: {e}") from e

        # Best-effort existence checks (fast fail)
        if spec.mode == "none":
            return

        plugin_root = path.parent.resolve()

        # requirements (required for online/bundle)
        req_rel = spec.requirements
        if not isinstance(req_rel, str) or not req_rel.strip():
            raise PluginLoadError(f"'deps.requirements' is required for mode={spec.mode} in {path}")

        req_p = (plugin_root / req_rel).resolve()
        if not req_p.exists():
            raise PluginLoadError(f"'deps.requirements' not found: {req_p}")
        self._ensure_under_root(plugin_root, req_p, what="'deps.requirements'", meta_id=manifest_id)

        # wheels_dir (required for bundle)
        if spec.mode == "bundle":
            wd_rel = spec.wheels_dir
            if not isinstance(wd_rel, str) or not wd_rel.strip():
                raise PluginLoadError(f"'deps.wheels_dir' is required for bundle mode in {path}")

            wd_p = (plugin_root / wd_rel).resolve()
            if not wd_p.exists():
                raise PluginLoadError(f"'deps.wheels_dir' not found: {wd_p}")
            self._ensure_under_root(plugin_root, wd_p, what="'deps.wheels_dir'", meta_id=manifest_id)
                
