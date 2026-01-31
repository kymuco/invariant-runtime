from __future__ import annotations

import hashlib
import json
import importlib.util
import sys
import logging
from pathlib import Path
from typing import Any, List, Optional, Type

from invariant.load.sources.base import PluginSource
from invariant.spec.meta import PluginMeta
from invariant.spec.descriptor import PluginDescriptor
from invariant.spec.source import PluginSourceSpec
from invariant.spec.plugin import BasePlugin
from invariant.spec.errors import PluginLoadError

# поправь путь импорта под твой проект
# from invariant.spec.dependency import DependencySpec, DependencyScope  # noqa: F401


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

    kind = "manifest"

    def __init__(self, search_paths: List[Path | str]):
        self.search_paths = [Path(p).resolve() for p in search_paths]

    # ------------------------------------------------------------------
    # DISCOVER
    # ------------------------------------------------------------------

    def discover(
        self,
        base_class: Optional[Type] = None,
        include_unstable: bool = False,
        *,
        dev_mode: bool = False,
        fail_fast: bool = False,
        **kwargs,
    ) -> List[PluginDescriptor]:
        descriptors: List[PluginDescriptor] = []

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
                        module = self._load_module(descriptor.source.module, descriptor.source.path)
                        cls = getattr(module, descriptor.source.class_name, None)
                        if not cls or not issubclass(cls, base_class):
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

    def _read_manifest(self, manifest_path: Path, *, dev_mode: bool) -> PluginDescriptor:
        data = self._read_json(manifest_path)
        self._validate_manifest_v1(data, manifest_path)

        source_block = data["source"]

        meta = self._parse_meta_v1(data, manifest_path)

        if (not dev_mode) and meta.version is None:
            raise PluginLoadError(
                f"Manifest plugin {meta.id} must declare version in strict mode: {manifest_path}",
                meta_id=meta.id,
            )

        code_path = (manifest_path.parent / source_block["code_path"]).resolve()
        if not code_path.exists():
            raise PluginLoadError(f"Plugin code file not found: {code_path}")

        module_name = self._build_module_name(manifest_path, meta)

        source = PluginSourceSpec(
            kind=self.kind,
            path=code_path,
            module=module_name,
            class_name=source_block["class_name"],
            extra={
                "manifest_path": str(manifest_path),
                "manifest_version": 1,
            },
        )

        return PluginDescriptor(meta=meta, source=source)

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
            raise PluginLoadError(f"Failed to read manifest: {manifest_path}", cause=e)

        if not isinstance(data, dict):
            raise PluginLoadError(f"Manifest root must be an object: {manifest_path}")

        return data

    def _parse_meta_v1(self, data: dict[str, Any], manifest_path: Path) -> PluginMeta:
        # Copy only fields that belong to PluginMeta (remove non-meta keys)
        meta_dict = dict(data)
        meta_dict.pop("manifest_version", None)
        meta_dict.pop("source", None)

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
            meta = PluginMeta(**meta_dict)
        except TypeError as e:
            raise PluginLoadError(f"Invalid PluginMeta in manifest: {manifest_path}", cause=e)

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

        module = self._load_module(source.module, source.path)

        try:
            cls = getattr(module, source.class_name)
        except AttributeError:
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
            )


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
            spec.loader.exec_module(module)  # type: ignore[attr-defined]
        except Exception as e:
            sys.modules.pop(module_name, None)
            logger.exception("Failed to load module %s from %s: %s", module_name, file, e)
            raise PluginLoadError(f"Failed to load module {module_name} from {file}", cause=e)

        return module

    def _validate_manifest_v1(self, data: dict[str, Any], path: Path) -> None:
        version = data.get("manifest_version")
        if version not in SUPPORTED_MANIFEST_VERSIONS:
            raise PluginLoadError(
                f"Unsupported manifest_version={version} in {path} (expected 1)"
            )

        if "id" not in data or not isinstance(data.get("id"), str) or not data["id"].strip():
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

        deps = data.get("dependencies", [])
        if deps is None:
            return
        if not isinstance(deps, list):
            raise PluginLoadError(f"'dependencies' must be list in {path}")
        for i, dep in enumerate(deps):
            if not isinstance(dep, dict):
                raise PluginLoadError(f"dependencies[{i}] must be object in {path}")
