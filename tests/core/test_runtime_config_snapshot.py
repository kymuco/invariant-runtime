from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

from invariant.api.policies import EphemeralPolicy
from invariant.api.service import PluginService
from invariant.catalog.registry import PluginRegistry
from invariant.load.env.env_manager import EnvInfo
from invariant.load.loader import PluginLoader
from invariant.load.sources.manifest import ManifestSource


class _FakeEnvManager:
    def ensure_env(self, *, plugin_key: str, plugin_root: Path, deps) -> EnvInfo:  # noqa: ANN001
        return EnvInfo(env_dir=plugin_root, python_exe=Path(sys.executable), key=f"fake:{plugin_key}")


def test_execution_runtime_injects_config_snapshot_for_inprocess_plugin(tmp_path: Path) -> None:
    service = _build_service(
        plugin_root=_write_plugin(
            tmp_path=tmp_path,
            plugin_id="tests:config_snapshot",
            config_prefix="demo.inprocess.",
            config_param=None,
            isolated=False,
        ),
        isolated=False,
    )
    try:
        with service.execution(
            policy=EphemeralPolicy(
                config={
                    "demo.inprocess.foo": "bar",
                    "demo.inprocess.num": 7,
                    "demo.other.ignore": "nope",
                }
            )
        ) as ctx:
            plugin = ctx.get("tests:config_snapshot")
            assert plugin.snapshot() == {"foo": "bar", "num": 7}
    finally:
        service.close()


def test_execution_runtime_injects_config_snapshot_for_isolated_plugin(tmp_path: Path) -> None:
    service = _build_service(
        plugin_root=_write_plugin(
            tmp_path=tmp_path,
            plugin_id="tests:isolated_config_snapshot",
            config_prefix="demo.isolated.",
            config_param="settings",
            isolated=True,
        ),
        isolated=True,
    )
    try:
        with service.execution(
            policy=EphemeralPolicy(
                config={
                    "demo.isolated.asset_root": "E:/demo/assets",
                    "demo.isolated.provider": "cpu",
                    "demo.other.ignore": "nope",
                }
            )
        ) as ctx:
            plugin = ctx.get("tests:isolated_config_snapshot")
            assert plugin.snapshot() == {
                "asset_root": "E:/demo/assets",
                "provider": "cpu",
            }
    finally:
        service.close()


def _build_service(*, plugin_root: Path, isolated: bool) -> PluginService:
    registry = PluginRegistry(mode="dev")
    env_manager = _FakeEnvManager() if isolated else None
    loader = PluginLoader([ManifestSource([plugin_root])], env_manager=env_manager)
    descriptors = loader.discover(mode="dev", fail_fast=True, include_unstable=True)
    for descriptor in descriptors:
        registry.register(descriptor)
    return PluginService(registry=registry, loader=loader, tracer=None)


def _write_plugin(
    *,
    tmp_path: Path,
    plugin_id: str,
    config_prefix: str,
    config_param: str | None,
    isolated: bool,
) -> Path:
    plugin_root = tmp_path / plugin_id.replace(":", "_")
    plugin_root.mkdir(parents=True, exist_ok=True)

    plugin_code = textwrap.dedent(
        """
        from invariant.spec.plugin import BasePlugin


        class ConfigSnapshotPlugin(BasePlugin):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._snapshot = {}

            def initialize(self, config=None, settings=None, **deps):
                selected = settings if settings is not None else config
                self._snapshot = dict(selected or {})

            def snapshot(self):
                return dict(self._snapshot)

            def shutdown(self):
                return None
        """
    ).strip()
    (plugin_root / "plugin.py").write_text(plugin_code, encoding="utf-8")

    manifest: dict[str, object] = {
        "manifest_version": 1,
        "id": plugin_id,
        "version": "0.1.0",
        "name": "Config Snapshot Test Plugin",
        "maturity": "stable",
        "runtime": {
            "config_prefix": config_prefix,
        },
        "source": {
            "code_path": "plugin.py",
            "class_name": "ConfigSnapshotPlugin",
        },
    }
    if config_param is not None:
        runtime = manifest["runtime"]
        assert isinstance(runtime, dict)
        runtime["config_param"] = config_param
    if isolated:
        manifest["deps"] = {
            "mode": "online",
            "requirements": "requirements.txt",
        }
        (plugin_root / "requirements.txt").write_text("", encoding="utf-8")

    (plugin_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return plugin_root
