"""Installed ZIP sources must use the ordinary Loader/Registry/Service path."""
from __future__ import annotations

import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from invariant.api import PluginService
from invariant.load.loader import PluginLoader
from invariant.load.plugin_store import LocalPluginStore, PluginStoreError
from invariant.load.sources.zip import ZipSource
from invariant.spec.errors import PluginLoadError


CORE = """from invariant.spec import BasePlugin
class Core(BasePlugin):
    def initialize(self, **deps): self.calls = 0
    def shutdown(self): self.stopped = True
    def call(self):
        self.calls += 1
        return self.calls
"""
VOICE = """from invariant.spec import BasePlugin
class Voice(BasePlugin):
    def initialize(self, *, core, **deps): self.core = core
    def shutdown(self): self.stopped = True
    def use(self): return self.core.call()
"""


def make_zip(root: Path, name: str, *, dependency: str | None = None,
             maturity: str = "stable", nested_manifest: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name.replace(':', '-')}.zip"
    manifest = {
        "manifest_version": 1,
        "id": name,
        "version": "1.0.0",
        "maturity": maturity,
        "source": {"code_path": "plugin.py", "class_name": "Core" if dependency is None else "Voice"},
    }
    if dependency:
        manifest["dependencies"] = [{
            "id": dependency, "param": "core", "scope": "SINGLETON",
        }]
    with zipfile.ZipFile(path, "w") as output:
        output.writestr("manifest.json", json.dumps(manifest))
        output.writestr("plugin.py", CORE if dependency is None else VOICE)
        if nested_manifest:
            output.writestr("nested/manifest.json", json.dumps({
                "manifest_version": 1, "id": "lab:uninstalled",
                "version": "1.0.0", "source": {
                    "code_path": "../plugin.py", "class_name": "Core",
                },
            }))
    return path


def test_store_service_is_zip_source_and_reuses_shared_dependency(tmp_path):
    root = tmp_path / "source"
    core = make_zip(root, "lab:core")
    voice_a = make_zip(root, "lab:voice-a", dependency="lab:core@1.0.0")
    voice_b = make_zip(root, "lab:voice-b", dependency="lab:core@1.0.0")
    store = LocalPluginStore(tmp_path / "store")
    store.install_zip(core)
    store.install_zip(voice_a)
    store.install_zip(voice_b)
    with store.open_service() as service:
        assert isinstance(service.loader, PluginLoader)
        assert len(service.loader.sources) == 1
        assert isinstance(service.loader.sources[0], ZipSource)
        assert service.loader.sources[0].env_manager is service.env_manager
        assert {descriptor.source.kind for descriptor in service.registry.list()} == {"zip"}
        a = service.get("lab:voice-a@1.0.0")
        b = service.get("lab:voice-b@1.0.0")
        assert a.use() == 1
        assert b.use() == 2
        assert a.core is b.core
    assert len(list(store.objects.iterdir())) == 3
    assert list(store.leases.iterdir()) == []


def test_zip_original_can_be_deleted_after_install(tmp_path):
    zip_path = make_zip(tmp_path, "lab:core")
    store = LocalPluginStore(tmp_path / "store")
    store.install_zip(zip_path)
    zip_path.unlink()
    with store.open_service() as service:
        assert service.get("lab:core@1.0.0").call() == 1


def test_nested_manifests_are_not_registered_as_another_plugin(tmp_path):
    archive = make_zip(tmp_path, "lab:core", nested_manifest=True)
    store = LocalPluginStore(tmp_path / "store")
    store.install_zip(archive)
    with store.open_service() as service:
        assert [item.id for item in service.list_plugins()] == ["lab:core"]


def test_lease_snapshot_survives_logical_uninstall_before_discover(tmp_path):
    archive = make_zip(tmp_path, "lab:core")
    store = LocalPluginStore(tmp_path / "store")
    store.install_zip(archive)
    with store.leased_plugin_roots() as snapshot:
        store.uninstall_root("lab:core@1.0.0")
        source = ZipSource(store=store, installed_roots=snapshot)
        descriptions = source.discover(fail_fast=True)
        assert [d.meta.id for d in descriptions] == ["lab:core"]
        assert store.prune_unreferenced(confirmed_untracked_consumers_stopped=True) == []
    assert len(store.prune_unreferenced(confirmed_untracked_consumers_stopped=True)) == 1


def test_zip_source_rejects_forged_code_path(tmp_path):
    archive = make_zip(tmp_path, "lab:core")
    store = LocalPluginStore(tmp_path / "store")
    store.install_zip(archive)
    source = ZipSource(store=store)
    descriptor = source.discover(fail_fast=True)[0]
    forged_source = replace(descriptor.source, path=tmp_path / "foreign.py")
    with pytest.raises(PluginLoadError, match="differs from installed manifest"):
        source.load(forged_source, descriptor=replace(descriptor, source=forged_source))


def test_direct_archive_paths_cannot_install_during_discovery(tmp_path):
    archive = make_zip(tmp_path, "lab:core")
    with pytest.raises(ValueError, match="Install local ZIP paths explicitly"):
        ZipSource([str(archive)])
    assert ZipSource().discover(fail_fast=True) == []
    assert not (tmp_path / "store").exists()


def test_zip_source_maturity_filter_uses_manifest_policy(tmp_path):
    archive = make_zip(tmp_path, "lab:core", maturity="beta")
    store = LocalPluginStore(tmp_path / "store")
    store.install_zip(archive)
    source = ZipSource(store=store)
    assert source.discover() == []
    assert [d.meta.id for d in source.discover(include_unstable=True)] == ["lab:core"]
    with store.open_service(include_unstable=True) as service:
        assert service.has_plugin("lab:core@1.0.0")
