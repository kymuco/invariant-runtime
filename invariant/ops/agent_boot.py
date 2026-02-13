from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from invariant.api.service import PluginService
from invariant.catalog.registry import PluginRegistry
from invariant.load.loader import PluginLoader
from invariant.load.sources.filesystem import FilesystemSource
from invariant.load.sources.manifest import MAX_MANIFEST_BYTES, ManifestSource
from invariant.ops.metrics import RuntimeMetrics
from invariant.ops.tracing import InMemoryRuntimeTracer
from invariant.spec.errors import PluginLoadError


class Utf8SigManifestSource(ManifestSource):
    def _read_json(self, manifest_path: Path) -> dict[str, Any]:
        try:
            st = manifest_path.stat()
            if st.st_size > MAX_MANIFEST_BYTES:
                raise PluginLoadError(f"Manifest too large (>1MB): {manifest_path}")
            raw = manifest_path.read_text(encoding="utf-8-sig")
            data = json.loads(raw)
        except PluginLoadError:
            raise
        except Exception as e:
            raise PluginLoadError(f"Failed to read manifest: {manifest_path}", cause=e) from e

        if not isinstance(data, dict):
            raise PluginLoadError(f"Manifest root must be an object: {manifest_path}")
        return data


def build_service() -> PluginService:
    registry = PluginRegistry(mode="dev")
    manifest_paths = [
        Path("manifests/schema_guard_plugin"),
        Path("manifests/repo_tools_plugin"),
        Path("manifests/quality_gate_plugin"),
        Path("manifests/g4f_openai_plugin"),
        Path("manifests/g4f_openai_service"),
        Path("manifests/llm_adapter_plugin"),
        Path("manifests/planner_llm_plugin"),
        Path("manifests/patch_proposer_llm_plugin"),
        Path("manifests/coding_agent_plugin"),
        Path("manifests/pipeline_runner_plugin"),
    ]

    loader = PluginLoader(
        [
            FilesystemSource([Path("plugins")]),
            Utf8SigManifestSource(manifest_paths),
        ]
    )
    descriptors = loader.discover(mode="dev", fail_fast=True)
    for d in descriptors:
        registry.register(d)

    tracer = InMemoryRuntimeTracer(
        metrics=RuntimeMetrics(),
        debug_touch_events=True,
        max_events_per_stream=4000,
    )
    return PluginService(registry=registry, loader=loader, tracer=tracer)
