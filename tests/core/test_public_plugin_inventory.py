from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from invariant.api import PluginInfo, PluginService


def _write_plugin(
    root: Path,
    *,
    plugin_id: str,
    version: str,
    name: str,
    categories: list[str],
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.py").write_text(
        "from invariant.spec import BasePlugin\n\n"
        "class ExamplePlugin(BasePlugin):\n"
        "    def initialize(self, **deps):\n"
        "        return None\n\n"
        "    def shutdown(self):\n"
        "        return None\n",
        encoding="utf-8",
    )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "id": plugin_id,
                "version": version,
                "name": name,
                "categories": categories,
                "source": {
                    "code_path": "plugin.py",
                    "class_name": "ExamplePlugin",
                },
            }
        ),
        encoding="utf-8",
    )
    return root


def _build_inventory_service(tmp_path: Path) -> PluginService:
    alpha_new = _write_plugin(
        tmp_path / "alpha-new",
        plugin_id="example:alpha",
        version="1.10",
        name="Alpha New",
        categories=["group-a", "shared"],
    )
    beta = _write_plugin(
        tmp_path / "beta",
        plugin_id="example:beta",
        version="2.0",
        name="Beta",
        categories=["group-b", "shared"],
    )
    alpha_old = _write_plugin(
        tmp_path / "alpha-old",
        plugin_id="example:alpha",
        version="1.2",
        name="Alpha Old",
        categories=["group-a"],
    )
    return PluginService.from_manifest_paths([alpha_new, beta, alpha_old])


def test_list_plugins_returns_stable_public_projection(tmp_path: Path) -> None:
    service = _build_inventory_service(tmp_path)
    try:
        inventory = service.list_plugins()
    finally:
        service.close()

    assert [(item.id, item.version) for item in inventory] == [
        ("example:alpha", "1.2.0"),
        ("example:alpha", "1.10.0"),
        ("example:beta", "2.0.0"),
    ]
    assert inventory[0] == PluginInfo(
        id="example:alpha",
        version="1.2.0",
        name="Alpha Old",
        maturity="stable",
        categories=("group-a",),
    )
    assert not hasattr(inventory[0], "source")
    assert not hasattr(inventory[0], "descriptor")


def test_list_plugins_filters_by_category_without_exposing_registry(tmp_path: Path) -> None:
    service = _build_inventory_service(tmp_path)
    try:
        group_a = service.list_plugins(category="group-a")
        shared = service.list_plugins(category="shared")
    finally:
        service.close()

    assert [(item.id, item.version) for item in group_a] == [
        ("example:alpha", "1.2.0"),
        ("example:alpha", "1.10.0"),
    ]
    assert [(item.id, item.version) for item in shared] == [
        ("example:alpha", "1.10.0"),
        ("example:beta", "2.0.0"),
    ]


def test_has_plugin_supports_base_and_concrete_references(tmp_path: Path) -> None:
    service = _build_inventory_service(tmp_path)
    try:
        assert service.has_plugin("example:alpha") is True
        assert service.has_plugin("example:alpha@1.10") is True
        assert service.has_plugin("example:alpha@9.0") is False
        assert service.has_plugin("example:missing") is False
        with pytest.raises(ValueError, match="plugin_id"):
            service.has_plugin("")
    finally:
        service.close()


def test_plugin_info_is_immutable() -> None:
    info = PluginInfo(id="example:alpha", version="1.0.0")
    with pytest.raises(FrozenInstanceError):
        info.name = "changed"  # type: ignore[misc]
