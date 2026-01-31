# invariant/exec/caps/inspector.py
from __future__ import annotations

from invariant.exec.caps.ids import CAP_FS_SANDBOX, CAP_WORKSPACE, CAP_PREFIX


class RuntimeInspector:
    """
    Safe, dict-only view over:
    - runtime/store state
    - registry descriptors
    - selected capabilities snapshot (sandbox/workspace)
    """

    def __init__(self, *, registry, runtime, capabilities=None):
        self._registry = registry
        self._runtime = runtime
        self._caps = capabilities  # CapabilityProvider or CapabilityView

    def health(self):
        policy = getattr(self._runtime, "policy", None)
        return {
            "runtime_id": getattr(self._runtime, "runtime_id", None),
            "closed": bool(getattr(self._runtime, "_closed", False)),
            "instance_mode": getattr(policy, "instance_mode", None),
        }

    def environment(self):
        return {
            "sandbox": self._fs_info(CAP_FS_SANDBOX),
            "workspace": self._fs_info(CAP_WORKSPACE),
        }

    def list_descriptors(self):
        out = []
        for d in self._registry.list():
            out.append(
                {
                    "id": d.meta.id,
                    "version": d.meta.version,
                    "name": getattr(d.meta, "name", None),
                    "maturity": getattr(d.meta, "maturity", None),
                    "categories": getattr(d.meta, "categories", None),
                    "dependencies": [dep.id for dep in (d.meta.dependencies or [])],
                }
            )
        return out

    def list_instances(self):
        inst = self._runtime.store.all_instances()
        out = []
        for k, v in inst.items():
            fsm = getattr(v, "fsm", None)
            ready_attr = getattr(fsm, "is_ready", None) if fsm else None
            ready = bool(ready_attr()) if callable(ready_attr) else (
                bool(ready_attr) if ready_attr is not None else None
            )
            out.append(
                {
                    "key": k,
                    "plugin_id": getattr(getattr(v, "meta", None), "id", None),
                    "version": getattr(getattr(v, "meta", None), "version", None),
                    "state": getattr(fsm, "state", None) if fsm else None,
                    "ready": ready,
                }
            )
        return out

    def dependency_graph(self):
        graph = {}
        for d in self._registry.list():
            plugin_deps = []
            cap_reqs = []
            for dep in (d.meta.dependencies or []):
                dep_id = getattr(dep, "id", None)
                if not dep_id:
                    continue
                if isinstance(dep_id, str) and dep_id.startswith(CAP_PREFIX):
                    cap_reqs.append(dep_id)
                else:
                    plugin_deps.append(dep_id)
            graph[d.meta.id] = {"plugins": plugin_deps, "capabilities": cap_reqs}
        return graph

    def get_state(self, plugin_id: str):
        try:
            key = self._runtime._canonical_key(plugin_id)
        except Exception:
            return {"id": plugin_id, "state": "unknown"}

        inst = self._runtime.store.get(key)
        if inst is None:
            return {"id": plugin_id, "state": "missing", "key": key}

        fsm = getattr(inst, "fsm", None)
        ready_attr = getattr(fsm, "is_ready", None) if fsm else None
        ready = bool(ready_attr()) if callable(ready_attr) else (
            bool(ready_attr) if ready_attr is not None else None
        )

        return {
            "id": plugin_id,
            "key": key,
            "state": getattr(fsm, "state", None) if fsm else None,
            "ready": ready,
        }

    def _fs_info(self, cap_id_base: str):
        cap = self._cap_get(cap_id_base)
        if cap is None:
            return None
        return {
            "root": str(getattr(cap, "root", None)),
            "allow_write": bool(getattr(cap, "allow_write", lambda: False)()),
            "allow_delete": bool(getattr(cap, "allow_delete", lambda: False)()),
            "max_read_bytes": int(getattr(cap, "max_read_bytes", lambda: 0)()),
            "max_write_bytes": int(getattr(cap, "max_write_bytes", lambda: 0)()),
        }

    def _cap_get(self, cap_id_base: str):
        if self._caps is None:
            return None
        try:
            return self._caps.get(cap_id_base)
        except Exception:
            return None
        