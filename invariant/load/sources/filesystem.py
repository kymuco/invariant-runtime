from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import sys
import importlib
import importlib.util
import inspect
import logging
from types import ModuleType
from typing import List, Optional, Type, Dict

from invariant.load.sources.base import PluginSource
from invariant.spec.meta import PluginMeta
from invariant.spec.plugin import BasePlugin
from invariant.spec.errors import PluginLoadError
from invariant.spec.descriptor import PluginDescriptor
from invariant.spec.source import PluginSourceSpec

logger = logging.getLogger(__name__)


class FilesystemSource(PluginSource):
    """
    Source для загрузки плагинов из файловой системы.

    ВАЖНО:
    - PluginMeta НЕ содержит module / class_name
    - ВСЕ загрузочные детали — в PluginSourceSpec
    """

    _IGNORE_DIRS = {".git", "__pycache__", ".venv", "env", "build", "dist"}

    def __init__(
        self,
        search_paths: List[Path] | List[str],
        project_root: Optional[Path] = None,
    ):
        self.search_paths = [Path(p) for p in search_paths]
        self.project_root = Path(project_root).resolve() if project_root else None
        self.kind = "filesystem"

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
        **kwargs
    ) -> List[PluginDescriptor]:
        descriptors: List[PluginDescriptor] = []

        for base in self.search_paths:
            if not base.exists():
                logger.debug("Plugin search path does not exist: %s", base)
                continue

            for py in base.rglob("*.py"):
                if any(part in self._IGNORE_DIRS for part in py.parts):
                    continue
                if py.name.startswith("test_") or "tests" in py.parts:
                    continue
                
                # old
                # module = self._safe_import(py, base)
                # if module is None:
                #     continue
                #

                
                # new
                module_name = self._compute_module_name(py)
                if not module_name:
                    logger.debug("Cannot compute module name for %s", py)
                    continue

                try:
                    module = self._load_module_from_path(module_name, py)
                except Exception as e:
                    logger.warning("Failed to load plugin module %s: %s", py, e)
                    if fail_fast:
                        raise
                    continue
                #


                for _, cls in inspect.getmembers(module, inspect.isclass):
                    if cls.__module__ != module.__name__:
                        continue
                    if not issubclass(cls, BasePlugin) or cls is BasePlugin:
                        continue

                    if base_class is not None:
                        if cls is base_class or not issubclass(cls, base_class):
                            continue
                    try:
                        meta = self._extract_meta(cls, module, py)

                        if (not dev_mode) and meta.version is None:
                            raise PluginLoadError(
                                f"Filesystem plugin {meta.id} must declare version in strict mode",
                                meta_id=meta.id,
                            )

                        if (
                            not include_unstable
                            and getattr(meta, "maturity", "stable") != "stable"
                        ):
                            continue

                        source = self._build_source_spec(
                            meta=meta,
                            cls=cls,
                            module=module,
                            py=py,
                            raw_meta=getattr(cls, "PLUGIN_META", None),
                        )

                        descriptors.append(
                            PluginDescriptor(
                                meta=meta,
                                source=source,
                                # runtime=ExecutionRuntime(),
                            )
                        )
                    except Exception:
                        logger.exception(
                            "Skipping invalid plugin candidate: file=%s module=%s class=%s",
                            py,
                            module.__name__,
                            getattr(cls, "__qualname__", getattr(cls, "__name__", str(cls))),
                        )
                        if fail_fast:
                            raise
                        continue
                    # print('='*20)
                    # print(descriptors)
                    # print('='*20)

        return descriptors

    # ------------------------------------------------------------------
    # CAN LOAD
    # ------------------------------------------------------------------
    def can_load(self, source: PluginSourceSpec) -> bool:
        return source.kind == "filesystem"

    # ------------------------------------------------------------------
    # LOAD
    # ------------------------------------------------------------------
    def load(
        self,
        source: PluginSourceSpec,
        registry=None,
        *,
        descriptor: PluginDescriptor | None = None,
        **kwargs,
    ) -> BasePlugin:

        if source.kind != "filesystem":
            raise PluginLoadError("FilesystemSource cannot load this source")

        if not source.module or not source.class_name:
            raise PluginLoadError(
                f"Incomplete filesystem source spec: {source}"
            )
        
        # new
        module = sys.modules.get(source.module)
        if not module:
            if not source.path:
                raise PluginLoadError(
                    f"Plugin module {source.module} was not loaded and has no path"
                )

            module = self._load_module_from_path(
                source.module,
                source.path,
            )
        

        # old
        # try:
        #     module = importlib.import_module(source.module)
        # except Exception:
        #     if not source.path:
        #         raise

        #     spec = importlib.util.spec_from_file_location(
        #         source.module,
        #         str(source.path),
        #     )
        #     if not spec or not spec.loader:
        #         raise PluginLoadError(
        #             f"Cannot load module {source.module}"
        #         )

        #     module = importlib.util.module_from_spec(spec)
        #     sys.modules[source.module] = module
        #     spec.loader.exec_module(module)  # type: ignore
        #

        cls = getattr(module, source.class_name, None)
        if not cls:
            raise PluginLoadError(
                f"Class {source.class_name} not found in {source.module}"
            )

        try:
            # Prefer injecting meta/context if possible (BasePlugin supports it)
            if descriptor is not None:
                try:
                    return cls(meta=descriptor.meta, context=None)
                except TypeError:
                    try:
                        return cls(meta=descriptor.meta)
                    except TypeError:
                        return cls()

            return cls()

        except TypeError as e:
            raise PluginLoadError(
                f"Plugin {source.class_name} has invalid constructor signature",
                cause=e,
            )


    # ------------------------------------------------------------------
    # META EXTRACTION
    # ------------------------------------------------------------------

    def _extract_meta(
        self,
        cls: Type,
        module: ModuleType,
        py: Path,
    ) -> PluginMeta:

        raw_meta = getattr(cls, "PLUGIN_META", None)

        if isinstance(raw_meta, PluginMeta):
            meta = raw_meta

            # если id пустой, создаем новый объект с нужным id
            if not meta.id:
                meta = replace(
                    meta,
                    id=self._build_id(
                        explicit_id=None,
                        cls=cls,
                        author=meta.author,
                    )
                )

        elif isinstance(raw_meta, dict):
            meta_id = raw_meta.get("id") or self._build_id(
                explicit_id=None,
                cls=cls,
                author=raw_meta.get("author"),
            )
            meta = PluginMeta(
                id=meta_id,
                name=raw_meta.get("name") or cls.__name__,
                version=raw_meta.get("version"),
                description=raw_meta.get("description") or cls.__doc__,
                categories=raw_meta.get("categories") or [getattr(cls, "_CATEGORY", "plugin")],
                maturity=raw_meta.get("maturity") or "stable",
                author=raw_meta.get("author") or "local",
            )

        else:
            # нет PLUGIN_META
            author = (
                getattr(cls, "AUTHOR", None)
                or getattr(module, "__author__", None)
                or "local"
            )
            meta = PluginMeta(
                id=self._build_id(explicit_id=None, cls=cls, author=author),
                name=getattr(cls, "name", cls.__name__),
                version=getattr(cls, "VERSION", None),
                description=getattr(cls, "__doc__", None),
                categories=[getattr(cls, "_CATEGORY", "plugin")],
                maturity="stable",
                author=author,
            )

        # проверка namespace
        if ":" not in meta.id:
            raise ValueError(
                f"Plugin id '{meta.id}' must contain namespace "
                f"(example: author:plugin)"
            )

        return meta


    # ------------------------------------------------------------------
    # ID BUILDER (НОВАЯ ЛОГИКА)
    # ------------------------------------------------------------------
    def _build_id(
        self,
        *,
        explicit_id: Optional[str],
        cls: Optional[type],
        author: Optional[str],
    ) -> str:
        """
        Новый контракт:
        - id НЕ зависит от class_name в meta
        - class используется ТОЛЬКО как эвристика
        """

        if explicit_id:
            return explicit_id

        author_part = (author or "local").lower().replace(" ", "_")

        if cls:
            name_part = cls.__name__.lower()
        else:
            name_part = "plugin"

        return f"{author_part}:{name_part}"

    # ------------------------------------------------------------------
    # SAFE IMPORT
    # ------------------------------------------------------------------
    # old deprecated
    def _safe_import(self, py: Path, base: Path):
        try:
            module_name = self._compute_module_name(py)
            if module_name:
                return importlib.import_module(module_name)
        except Exception as e:
            logger.debug("Failed to import %s via module name: %s", py, e)

        try:
            spec_name = (
                f"codexia.plugins.{'.'.join(py.with_suffix('').parts[-2:])}"
            )
            spec = importlib.util.spec_from_file_location(
                spec_name,
                str(py),
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)  # type: ignore
                return module
        except Exception as e:
            logger.warning("Failed to import plugin file %s: %s", py, e)

        return None
    
    def _compute_module_name(self, py: Path) -> str:
        """
        Stable synthetic module name for a file-based plugin.
        Avoids relying on package layout and prevents collisions.
        """
        py = py.resolve()
        h = hashlib.sha256(str(py).encode("utf-8")).hexdigest()[:16]
        return f"invariant_plugin_{h}"

    # new
    # def _compute_module_name(self, py: Path) -> str | None:
    #     """
    #     Вычисляет имя модуля по пути файла.
    #     fallback: если project_root не задан, берём родителя файла как корень.
    #     """
    #     try:
    #         py = py.resolve()
    #         if self.project_root:
    #             root = self.project_root.resolve()
    #         else:
    #             root = py.parent.parent.parent  # fallback, берем родителя родителя
    #         rel = py.relative_to(root)
    #         mod = ".".join(rel.with_suffix("").parts)

    #         return f"{root.name}.{mod}"
    #     except Exception:
    #         logger.debug(f"Cannot compute module name for {py}")
    #         return None

    # old
    # def _compute_module_name(self, py: Path) -> Optional[str]:
    #     if not self.project_root:
    #         return None
    #     try:
    #         rel = py.relative_to(self.project_root)
    #         return ".".join(rel.with_suffix("").parts)
    #     except Exception:
    #         return None
    #
        
    # new actual
    def _load_module_from_path(
        self,
        module_name: str,
        path: Path,
    ) -> ModuleType:
        """
        Единственная точка загрузки python-модуля из файла.
        Инвариант: один module_name → один module object.
        """
        if module_name in sys.modules:
            return sys.modules[module_name]

        spec = importlib.util.spec_from_file_location(
            module_name,
            str(path),
        )
        if not spec or not spec.loader:
            raise PluginLoadError(
                f"Cannot create module spec for {module_name}"
            )

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module

        try:
            spec.loader.exec_module(module)  # type: ignore
        except Exception as e:
            sys.modules.pop(module_name, None)
            logger.exception(f"Failed to load module {module_name} from {path}: {e}")
            raise PluginLoadError(
                f"Failed to load module {module_name} from {path}",
                cause=e,
            )

        return module


    # ------------------------------------------------------------------
    # SOURCE SPEC BUILDER (ЕДИНСТВЕННАЯ ТОЧКА LEGACY)
    # ------------------------------------------------------------------
    def _build_source_spec(
        self,
        *,
        meta: PluginMeta,
        cls: Optional[type],
        module: Optional[ModuleType],
        py: Optional[Path],
        raw_meta: Optional[dict],
    ) -> PluginSourceSpec:

        legacy_used: list[str] = []

        if not py:
            raise PluginLoadError(
                f"Filesystem plugin {meta.id} has no file path"
            )

        module_name: Optional[str] = None
        class_name: Optional[str] = None

        # legacy raw_meta
        if isinstance(raw_meta, dict):
            if isinstance(raw_meta.get("module"), str):
                module_name = raw_meta["module"]
                legacy_used.append("module")
            if isinstance(raw_meta.get("class_name"), str):
                class_name = raw_meta["class_name"]
                legacy_used.append("class_name")

        # рефлексия
        if cls:
            module_name = module_name or cls.__module__
            class_name = class_name or cls.__name__

        if not module_name or not class_name:
            raise PluginLoadError(
                f"Cannot resolve module/class for plugin {meta.id}"
            )

        extra: dict = {}
        if legacy_used:  # надо разобратся
            logger.warning(
                "Plugin %s uses deprecated meta fields: %s",
                meta.id,
                ", ".join(legacy_used),
            )
            extra["legacy_meta"] = legacy_used

        return PluginSourceSpec(
            kind="filesystem",
            path=py.resolve(),
            module=module_name,
            class_name=class_name,
            extra=extra,
        )
