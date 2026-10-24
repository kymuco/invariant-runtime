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
from invariant.load.sources.installed import InstalledSource
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


def test_store_service_uses_verified_installed_source_and_reuses_shared_dependency(tmp_path):
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
        assert isinstance(service.loader.sources[0], InstalledSource)
        assert service.loader.sources[0].env_manager is service.env_manager
        assert {descriptor.source.kind for descriptor in service.registry.list()} == {"installed"}
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
        source = InstalledSource(store, snapshot)
        descriptions = source.discover(fail_fast=True)
        assert [d.meta.id for d in descriptions] == ["lab:core"]
        assert store.prune_unreferenced(confirmed_untracked_consumers_stopped=True) == []
    assert len(store.prune_unreferenced(confirmed_untracked_consumers_stopped=True)) == 1


def test_installed_source_rejects_forged_code_path(tmp_path):
    archive = make_zip(tmp_path, "lab:core")
    store = LocalPluginStore(tmp_path / "store")
    store.install_zip(archive)
    source = InstalledSource(store, store.plugin_roots())
    descriptor = source.discover(fail_fast=True)[0]
    forged_source = replace(descriptor.source, path=tmp_path / "foreign.py")
    with pytest.raises(PluginLoadError, match="differs from installed manifest"):
        source.load(forged_source, descriptor=replace(descriptor, source=forged_source))


def test_archive_zip_source_remains_separate_from_installed_runtime(tmp_path):
    archive = make_zip(tmp_path, "lab:core")
    # Direct archive discovery is not an implicit installation route.
    assert ZipSource([str(archive)]).discover(fail_fast=True) == []
    assert not (tmp_path / "store").exists()


def test_installed_source_uses_registry_canonical_versions(tmp_path):
    root = tmp_path / "source"
    archive = make_zip(root, "lab:core")
    with zipfile.ZipFile(archive) as original:
        manifest = json.loads(original.read("manifest.json"))
        code = original.read("plugin.py")
    manifest["version"] = "v1.0"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("manifest.json", json.dumps(manifest))
        output.writestr("plugin.py", code)
    store = LocalPluginStore(tmp_path / "store")
    store.install_zip(archive)
    with store.open_service() as service:
        assert service.get("lab:core@1.0.0").call() == 1


def test_installed_source_maturity_filter_uses_manifest_policy(tmp_path):
    archive = make_zip(tmp_path, "lab:core", maturity="beta")
    store = LocalPluginStore(tmp_path / "store")
    store.install_zip(archive)
    source = InstalledSource(store, store.plugin_roots())
    assert source.discover() == []
    assert [d.meta.id for d in source.discover(include_unstable=True)] == ["lab:core"]
    with store.open_service(include_unstable=True) as service:
        assert service.has_plugin("lab:core@1.0.0")


def test_loader_routes_each_zip_descriptor_to_its_own_store(tmp_path):
    from invariant.catalog.registry import PluginRegistry

    archive_a = make_zip(tmp_path / "a", "lab:core-a")
    archive_b = make_zip(tmp_path / "b", "lab:core-b")
    store_a = LocalPluginStore(tmp_path / "store-a")
    store_b = LocalPluginStore(tmp_path / "store-b")
    store_a.install_zip(archive_a)
    store_b.install_zip(archive_b)
    first = InstalledSource(store_a, store_a.plugin_roots())
    second = InstalledSource(store_b, store_b.plugin_roots())
    loader = PluginLoader([first, second])
    descriptors = loader.discover(mode="strict", fail_fast=True)
    assert len(descriptors) == 2
    assert first.can_load(next(d.source for d in descriptors if d.meta.id == "lab:core-a"))
    assert not first.can_load(next(d.source for d in descriptors if d.meta.id == "lab:core-b"))
    assert second.can_load(next(d.source for d in descriptors if d.meta.id == "lab:core-b"))
    registry = PluginRegistry(mode="strict")
    for descriptor in descriptors:
        registry.register(descriptor)
    service = PluginService(registry=registry, loader=loader)
    try:
        assert service.get("lab:core-a@1.0.0").call() == 1
        assert service.get("lab:core-b@1.0.0").call() == 1
    finally:
        # Explicit managed shutdown keeps this internal test lifecycle correct.
        service.shutdown("lab:core-a@1.0.0")
        service.shutdown("lab:core-b@1.0.0")
        service.close()


def test_zip_load_rehashes_only_selected_object(tmp_path, monkeypatch):
    source_root = tmp_path / "archives"
    core = make_zip(source_root, "lab:core")
    voice = make_zip(source_root, "lab:voice", dependency="lab:core@1.0.0")
    store = LocalPluginStore(tmp_path / "store")
    store.install_zip(core)
    store.install_zip(voice)
    visited = []
    original = store._verify_object

    def record_verification(digest):
        visited.append(digest)
        return original(digest)

    monkeypatch.setattr(store, "_verify_object", record_verification)
    with store.open_service() as service:
        # Snapshot validation is allowed to inspect all objects.
        visited.clear()
        assert service.get("lab:voice@1.0.0").use() == 1
        # Each activated instance verifies its own object once, not the
        # whole Store on each plugin load.
        assert len(visited) == 2
        assert len(set(visited)) == 2


def test_direct_discovery_load_accepts_uncanonical_version(tmp_path):
    archive = make_zip(tmp_path, "lab:core")
    with zipfile.ZipFile(archive) as original:
        manifest = json.loads(original.read("manifest.json"))
        code = original.read("plugin.py")
    manifest["version"] = "v1.0"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("manifest.json", json.dumps(manifest))
        output.writestr("plugin.py", code)
    store = LocalPluginStore(tmp_path / "store")
    store.install_zip(archive)
    loader = PluginLoader([InstalledSource(store, store.plugin_roots())])
    descriptor = loader.discover(mode="strict", fail_fast=True)[0]
    assert descriptor.meta.version == "v1.0"
    instance = loader.load(descriptor)
    assert type(instance).__name__ == "Core"


def test_isolated_zip_rejects_tampering_before_worker_or_env_setup(tmp_path, monkeypatch):
    archive = make_zip(tmp_path, "lab:core")
    with zipfile.ZipFile(archive) as original:
        manifest = json.loads(original.read("manifest.json"))
        code = original.read("plugin.py")
    manifest["deps"] = {"mode": "online", "requirements": "requirements.txt"}
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("manifest.json", json.dumps(manifest))
        output.writestr("plugin.py", code)
        output.writestr("requirements.txt", "# no external deps\n")
    store = LocalPluginStore(tmp_path / "store")
    store.install_zip(archive)
    loader = PluginLoader([InstalledSource(store, store.plugin_roots())])
    assert loader.sources[0].env_manager is loader._env_manager
    descriptor = loader.discover(mode="strict", fail_fast=True)[0]
    from invariant.load.isolated_proxy import IsolatedPluginProxy

    def unexpected_worker(*args, **kwargs):
        pytest.fail("isolated worker bypassed InstalledSource verification")

    monkeypatch.setattr(IsolatedPluginProxy, "from_descriptor", unexpected_worker)
    target = store.objects / store.state()["plugins"]["lab:core@1.0.0"] / "plugin.py"
    target.chmod(0o644)
    target.write_text("# corrupted after discovery\n", encoding="utf-8")
    with pytest.raises(PluginStoreError, match="installed plugin file modified"):
        loader.load(descriptor)
