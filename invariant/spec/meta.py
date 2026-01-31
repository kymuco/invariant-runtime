# invariant/spec/meta.py
from __future__ import annotations

from typing import Mapping, Any
from dataclasses import dataclass, field
from packaging.version import Version, InvalidVersion

from invariant.spec.dependency import DependencySpec, DependencyScope
from invariant.spec.ids import canonical_plugin_id, canonical_id_or_ref, base_id
# or replace to `canonical_plugin_id`

_ALLOWED_MATURITY = {"experimental", "beta", "stable", "deprecated"}


def _parse_dep(x: Any) -> DependencySpec:
    if isinstance(x, DependencySpec):
        return x
    if isinstance(x, Mapping):
        dep_id = x.get("id")
        param = x.get("param")
        if not isinstance(dep_id, str) or not dep_id.strip():
            raise ValueError("dependency.id must be non-empty string")
        if not isinstance(param, str) or not param.strip():
            raise ValueError("dependency.param must be non-empty string")
        if not param.isidentifier():
            raise ValueError(f"dependency.param must be valid identifier, got {param!r}")

        scope = x.get("scope", "EXECUTION")
        if isinstance(scope, str):
            try:
                scope = DependencyScope[scope.upper()]
            except KeyError as e:
                raise ValueError(f"bad dependency.scope: {scope!r}") from e
        if not isinstance(scope, DependencyScope):
            raise ValueError("dependency.scope must be DependencyScope or valid string name")

        optional_raw = x.get("optional", False)
        if not isinstance(optional_raw, bool):
            raise ValueError("dependency.optional must be boolean")

        return DependencySpec(
            id=canonical_id_or_ref(dep_id),
            param=param,
            scope=scope,
            optional=optional_raw,
        )

    raise TypeError(f"dependency item must be DependencySpec or mapping, got {type(x)}")

def _clean_str_tuple(xs: Any, *, field_name: str) -> tuple[str, ...]:
    out: list[str] = []
    for x in (xs or ()):
        if not isinstance(x, str):
            raise ValueError(f"{field_name} must contain only strings")
        s = x.strip()
        if not s:
            continue
        out.append(s)
    return tuple(out)


@dataclass(frozen=True, slots=True)
class PluginMeta:
    """
    Public, immutable plugin metadata.
    Safe to expose to users.
    """

    id: str
    version: str | None = None
    name: str | None = None
    description: str | None = None

    dependencies: tuple[DependencySpec, ...] = field(default_factory=tuple)

    categories: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    author: str | None = None
    maturity: str | None = "stable"  # experimental | beta | stable | deprecated
    
    repo: str | None = None
    priority: int = 0

    # extra: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("PluginMeta.id must be non-empty string")
        object.__setattr__(self, "id", canonical_id_or_ref(self.id))

        if self.version is not None:
            if not isinstance(self.version, str):
                raise ValueError("PluginMeta.version must be string or None")
            try:
                Version(self.version)  # fail fast
            except InvalidVersion as e:
                raise ValueError(f"bad version format: {self.version!r}") from e

        m = self.maturity
        if m is not None and not isinstance(m, str):
            raise ValueError("PluginMeta.maturity must be string or None")
        maturity = (m or "stable").strip().lower()
        if maturity not in _ALLOWED_MATURITY:
            raise ValueError(f"bad maturity: {maturity!r}")
        object.__setattr__(self, "maturity", maturity)

        deps = tuple(_parse_dep(x) for x in (self.dependencies or ()))
        seen_params = set()
        for d in deps:
            if d.param in seen_params:
                raise ValueError(f"duplicate dependency.param: {d.param!r}")
            seen_params.add(d.param)
        object.__setattr__(self, "dependencies", deps)

        object.__setattr__(self, "categories", _clean_str_tuple(self.categories, field_name="categories"))
        object.__setattr__(self, "tags", _clean_str_tuple(self.tags, field_name="tags"))

    # ----------------- utils -----------------

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "dependencies": [
                {"id": d.id, "param": d.param, "scope": d.scope.name, "optional": d.optional}
                for d in self.dependencies
            ],
            "categories": list(self.categories),
            "tags": list(self.tags),
            "author": self.author,
            "maturity": self.maturity,
            "repo": self.repo,
            "priority": self.priority,
        }

    def matches_category(self, category: str) -> bool:
        return category in self.categories

    def describe(self) -> str:
        return (
            f"{self.id} ({self.name or 'n/a'}) "
            f"[Categories: {', '.join(self.categories) or 'none'}] "
            f"v{self.version or 'n/a'} "
            f"Priority: {self.priority}"
        )

    def version_tuple(self) -> Version | None:
        if not self.version:
            return None
        try:
            return Version(self.version)
        except InvalidVersion:
            return None

    def is_newer_than(self, other: "PluginMeta") -> bool:
        self_v = self.version_tuple()
        other_v = other.version_tuple()
        if self_v and other_v:
            return self_v > other_v
        return False

    def has_dependency(self, plugin_id: str) -> bool:
        pid = canonical_plugin_id(plugin_id)
        return any(base_id(dep.id) == pid for dep in self.dependencies)

    # def with_extra(self, key: str, value: object) -> "PluginMeta":
    #     new_extra = dict(self.extra)
    #     new_extra[key] = value
    #     return PluginMeta(
    #         id=self.id,
    #         version=self.version,
    #         name=self.name,
    #         description=self.description,
    #         dependencies=self.dependencies,
    #         categories=self.categories,
    #         tags=self.tags,
    #         author=self.author,
    #         maturity=self.maturity,
    #         repo=self.repo,
    #         priority=self.priority,
    #         extra=new_extra,
    #     )
