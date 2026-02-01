# invariant/exec/resolver.py
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from invariant.exec.caps.registry import is_capability_ref
from invariant.spec.descriptor import PluginDescriptor
from invariant.spec.errors import DependencyCycleError, PluginNotFoundError
from invariant.spec.ids import base_id

EdgeFilter = Callable[[str, Any], bool]


class DependencyResolver:
    """
    Pure dependency graph builder.

    Responsibilities:
    - collect dependency graph
    - detect cycles
    - provide execution order

    Supports:
    - edge_filter(parent_id, spec) -> bool
      Use it to inline only dependencies that belong to the current policy/runtime.
    """

    def __init__(self, *, registry, edge_filter: EdgeFilter | None = None) -> None:
        self._registry = registry
        self._edge_filter = edge_filter
        self._graph: dict[str, set[str]] = {}
        self._caps: dict[str, set[str]] = {}
        self._visited: set[str] = set()
        self._stack: set[str] = set()

    # ---------- public API ----------

    def collect(self, root_id: str) -> None:
        self._graph.clear()
        self._caps.clear()
        self._visited.clear()
        self._stack.clear()
        self._dfs(root_id)

    def dependencies_of(self, plugin_id: str) -> list[str]:
        pid = self._canonical(plugin_id)
        return sorted(self._graph.get(pid, set()))

    def capabilities_of(self, plugin_id: str) -> list[str]:
        pid = self._canonical(plugin_id)
        return sorted(self._caps.get(pid, set()))

    def execution_order(self) -> list[str]:
        """
        Topological sort: dependencies first.
        """
        visited: set[str] = set()
        order: list[str] = []

        def visit(node: str):
            if node in visited:
                return
            visited.add(node)
            for dep in sorted(self._graph.get(node, set())):
                visit(dep)
            order.append(node)

        for node in sorted(self._graph.keys()):
            visit(node)

        return order

    # ---------- internal ----------

    def _dfs(self, plugin_ref: str) -> None:
        d: PluginDescriptor = self._registry.resolve(plugin_ref)
        v = getattr(getattr(d, "meta", None), "version", None)
        if not v:
            raise RuntimeError(f"Resolved descriptor has no version: {d.meta.id!r}")

        plugin_id = f"{d.meta.id}@{v}"
        plugin_id = self._canonical(plugin_id)  # если хочешь оставить нормализацию формата
        if plugin_id in self._stack:
            raise DependencyCycleError(
                f"Dependency cycle detected at {plugin_id}",
                meta_id=base_id(plugin_id),
            )

        if plugin_id in self._visited:
            return

        self._stack.add(plugin_id)

        try:
            # descriptor: PluginDescriptor = self._registry.resolve(plugin_id)
            self._graph.setdefault(plugin_id, set())
            self._caps.setdefault(plugin_id, set())

            for spec in d.meta.dependencies:
                dep_id = spec.id

                # Capabilities are not plugins: skip graph edges entirely
                if is_capability_ref(dep_id):
                    self._caps[plugin_id].add(dep_id)
                    continue

                # Resolve dependency to canonical id (pins version / normalizes latest)
                try:
                    dep_canon = self._canonical(dep_id)
                except (PluginNotFoundError, ValueError):
                    if spec.optional:
                        continue
                    raise

                # Policy/scope-aware inlining:
                if self._edge_filter is not None:
                    if not self._edge_filter(plugin_id, spec):
                        continue

                self._graph[plugin_id].add(dep_canon)
                self._dfs(dep_canon)

            self._visited.add(plugin_id)

        finally:
            self._stack.remove(plugin_id)

    def _canonical(self, plugin_ref: str) -> str:
        """
        Convert arbitrary plugin reference to canonical id@version for graph keys.
        Must be pinned to an exact version after resolve().
        """
        d: PluginDescriptor = self._registry.resolve(plugin_ref)
        v = getattr(getattr(d, "meta", None), "version", None)

        if not v:
            # If this happens, registry violated invariants (it must assign local versions in dev).
            raise RuntimeError(f"Resolved descriptor has no version: {d.meta.id!r}")

        return f"{d.meta.id}@{v}"

