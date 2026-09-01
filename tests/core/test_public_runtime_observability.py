from __future__ import annotations

import json
from pathlib import Path

import pytest

from invariant.api import PluginService, RuntimeObservabilityOptions


def _write_manifest_plugin(root: Path) -> Path:
    plugin_root = root / "hello"
    plugin_root.mkdir()
    (plugin_root / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "id": "example:hello",
                "version": "1.0.0",
                "source": {
                    "code_path": "plugin.py",
                    "class_name": "HelloPlugin",
                },
            }
        ),
        encoding="utf-8",
    )
    (plugin_root / "plugin.py").write_text(
        "from invariant.spec import BasePlugin\n\n"
        "class HelloPlugin(BasePlugin):\n"
        "    def initialize(self, **deps) -> None:\n"
        "        pass\n\n"
        "    def shutdown(self) -> None:\n"
        "        pass\n",
        encoding="utf-8",
    )
    return plugin_root


def test_runtime_observability_options_validate_stream_bound() -> None:
    with pytest.raises(ValueError, match="max_events_per_stream must be >= 10"):
        RuntimeObservabilityOptions(max_events_per_stream=9)


def test_manifest_service_materializes_standard_observability(tmp_path: Path) -> None:
    plugin_root = _write_manifest_plugin(tmp_path)
    service = PluginService.from_manifest_paths(
        [plugin_root],
        observability=RuntimeObservabilityOptions(
            debug_touch_events=True,
            max_events_per_stream=4000,
        ),
    )
    try:
        tracer = service.tracer
        assert tracer is not None
        assert type(tracer).__module__ == "invariant.ops.tracing"
        assert getattr(tracer, "_debug_touch") is True
        assert getattr(tracer, "_max_events") == 4000
        assert getattr(tracer, "metrics", None) is not None
    finally:
        service.close()


def test_custom_tracer_and_standard_observability_are_mutually_exclusive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="tracer and observability are mutually exclusive"):
        PluginService.from_manifest_paths(
            [tmp_path],
            tracer=object(),
            observability=RuntimeObservabilityOptions(),
        )
