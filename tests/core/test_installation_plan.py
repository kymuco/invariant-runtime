"""Offline, host-approved plugin installation graph and transaction checks."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from invariant.load import acquisition
from invariant.load.installation_plan import (
    ArtifactOffer, InstallationPlanError, PluginInstallationPlanner,
)
from invariant.load.plugin_store import LocalPluginStore, PluginStoreError

CORE = """from invariant.spec import BasePlugin
class Core(BasePlugin):
    def initialize(self, **deps): self.count = 0
    def shutdown(self): pass
    def call(self):
        self.count += 1
        return self.count
"""
VOICE = """from invariant.spec import BasePlugin
class Voice(BasePlugin):
    def initialize(self, *, core, **deps): self.core = core
    def shutdown(self): pass
    def call(self): return self.core.call()
"""


def archive(ref, *, dependencies=(), optional=(), capability=()):
    name, version = ref.rsplit("@", 1)
    manifest = {
        "manifest_version": 1,
        "id": name, "version": version,
        "source": {"code_path": "plugin.py",
                   "class_name": "Voice" if dependencies else "Core"},
        "dependencies": [
            {"id": dep, "param": "core", "scope": "SINGLETON"}
            for dep in dependencies
        ] + [{"id": dep, "param": "option", "optional": True}
             for dep in optional
        ] + [{"id": dep, "param": "capability"} for dep in capability],
    }
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest))
        z.writestr("plugin.py", VOICE if dependencies else CORE)
    return target.getvalue()


def offers(blobs, *, deps=None):
    deps = deps or {}
    return [ArtifactOffer(ref, f"https://plugins.example/{i}.zip",
                          hashlib.sha256(blob).hexdigest(), tuple(deps.get(ref, ())))
            for i, (ref, blob) in enumerate(blobs.items())]


class Response(io.BytesIO):
    status = 200
    headers = {"Content-Encoding": "identity"}

    def __init__(self, url, blob):
        super().__init__(blob)
        self.url = url

    def geturl(self):
        return self.url


def transport(monkeypatch, data, on_request=None):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            if on_request:
                on_request(request.full_url)
            return Response(request.full_url, data[request.full_url])

    monkeypatch.setattr(acquisition.urllib.request, "build_opener",
                        lambda *handlers: Opener())
    return calls


def planner(tmp_path, items):
    store = LocalPluginStore(tmp_path / "store")
    return store, PluginInstallationPlanner(
        store, items, allowed_origins=("https://plugins.example",),
    )


def test_plan_is_read_only_and_fetches_nothing(tmp_path, monkeypatch):
    ref = "lab:core@1.0.0"
    blob = archive(ref)
    item = offers({ref: blob})[0]
    store, p = planner(tmp_path, [item])
    calls = transport(monkeypatch, {item.url: blob})
    plan = p.plan([ref])
    assert plan.downloads == (item,)
    assert plan.installed == ()
    assert len(plan.plan_id) == 64
    assert calls == []
    assert store.state()["plugins"] == {}


def test_two_consumers_share_one_dependency_in_single_catalog_commit(tmp_path, monkeypatch):
    core, a, b = "lab:core@1.0.0", "lab:voice-a@1.0.0", "lab:voice-b@1.0.0"
    blobs = {core: archive(core), a: archive(a, dependencies=(core,)),
             b: archive(b, dependencies=(core,))}
    items = offers(blobs, deps={a: (core,), b: (core,)})
    store, p = planner(tmp_path, items)
    calls = transport(monkeypatch, {item.url: blobs[item.ref] for item in items})
    plan = p.plan([b, a])
    assert tuple(item.ref for item in plan.downloads) == (core, a, b)
    result = p.apply(plan, approved_plan_id=plan.plan_id)
    assert set(result["new_objects"]) == set(blobs)
    assert len(calls) == 3 and len(store.state()["plugins"]) == 3
    with store.open_service() as service:
        first, second = service.get(a), service.get(b)
        assert first.call() == 1
        assert second.call() == 2
        assert first.core is second.core
    assert store.state()["roots"] == [a, b]


def test_no_approval_means_no_fetch_or_catalog_mutation(tmp_path, monkeypatch):
    ref = "lab:core@1.0.0"
    blob = archive(ref)
    item = offers({ref: blob})[0]
    store, p = planner(tmp_path, [item])
    calls = transport(monkeypatch, {item.url: blob})
    plan = p.plan([ref])
    for approval in ("", "0" * 64):
        with pytest.raises(InstallationPlanError, match="approval"):
            p.apply(plan, approved_plan_id=approval)
    assert calls == []
    assert store.state()["plugins"] == {}


def test_missing_offer_and_dependency_cycle_fail_during_preview(tmp_path, monkeypatch):
    a, b = "lab:a@1.0.0", "lab:b@1.0.0"
    blobs = {a: archive(a), b: archive(b)}
    items = offers(blobs, deps={a: (b,), b: (a,)})
    _, p = planner(tmp_path, items)
    calls = transport(monkeypatch, {item.url: blobs[item.ref] for item in items})
    with pytest.raises(InstallationPlanError, match="cycle"):
        p.plan([a])
    _, missing = planner(tmp_path / "other", items[:1])
    with pytest.raises(InstallationPlanError, match="missing required"):
        missing.plan([a])
    assert calls == []


@pytest.mark.parametrize("wrong_kind", ["dependencies", "identity"])
def test_offer_disagrees_with_downloaded_manifest_no_publish(tmp_path, monkeypatch, wrong_kind):
    core, voice = "lab:core@1.0.0", "lab:voice@1.0.0"
    if wrong_kind == "dependencies":
        ref = voice
        blob = archive(voice, dependencies=(core,))
        match = "dependencies differ"
    else:
        ref = core
        blob = archive("lab:other@1.0.0")
        match = "identity differs"
    item = ArtifactOffer(ref, "https://plugins.example/archive.zip",
                         hashlib.sha256(blob).hexdigest())
    store, p = planner(tmp_path, [item])
    calls = transport(monkeypatch, {item.url: blob})
    plan = p.plan([ref])
    with pytest.raises(InstallationPlanError, match=match):
        p.apply(plan, approved_plan_id=plan.plan_id)
    assert calls == [item.url]
    assert store.state()["plugins"] == {}
    assert list(store.objects.iterdir()) == []


def test_later_download_failure_leaves_no_partial_install(tmp_path, monkeypatch):
    core, voice = "lab:core@1.0.0", "lab:voice@1.0.0"
    blobs = {core: archive(core), voice: archive(voice, dependencies=(core,))}
    items = offers(blobs, deps={voice: (core,)})
    store, p = planner(tmp_path, items)
    calls = transport(monkeypatch, {items[0].url: blobs[core], items[1].url: b"bad"})
    plan = p.plan([voice])
    with pytest.raises(acquisition.PluginAcquisitionError, match="SHA-256"):
        p.apply(plan, approved_plan_id=plan.plan_id)
    assert len(calls) == 2
    assert store.state()["plugins"] == {}
    assert list(store.objects.iterdir()) == []


def test_transitive_installed_dependency_promoted_without_fetch(tmp_path, monkeypatch):
    core, voice = "lab:core@1.0.0", "lab:voice@1.0.0"
    blobs = {core: archive(core), voice: archive(voice, dependencies=(core,))}
    store, p = planner(tmp_path, offers(blobs, deps={voice: (core,)}))
    one, two = tmp_path / "core.zip", tmp_path / "voice.zip"
    one.write_bytes(blobs[core])
    two.write_bytes(blobs[voice])
    store.install([one, two], roots=[voice])
    calls = transport(monkeypatch, {})
    plan = p.plan([core])
    assert plan.downloads == ()
    assert plan.installed == (core,)
    assert p.apply(plan, approved_plan_id=plan.plan_id)["new_objects"] == []
    assert set(store.state()["roots"]) == {voice, core}
    assert calls == []


def test_optional_and_capability_refs_do_not_require_downloads(tmp_path, monkeypatch):
    ref = "lab:core@1.0.0"
    blob = archive(ref, optional=("lab:optional@1.0.0",),
                   capability=("Invariant:Capability.fs_sandbox@0.3.0",))
    item = offers({ref: blob})[0]
    _, p = planner(tmp_path, [item])
    calls = transport(monkeypatch, {item.url: blob})
    plan = p.plan([ref])
    p.apply(plan, approved_plan_id=plan.plan_id)
    assert calls == [item.url]


def test_preview_invalidated_by_other_host_install_before_fetch(tmp_path, monkeypatch):
    ref, other = "lab:core@1.0.0", "lab:other@1.0.0"
    blob = archive(ref)
    item = offers({ref: blob})[0]
    store, p = planner(tmp_path, [item])
    plan = p.plan([ref])
    existing = tmp_path / "other.zip"
    existing.write_bytes(archive(other))
    store.install_zip(existing)
    calls = transport(monkeypatch, {item.url: blob})
    with pytest.raises(InstallationPlanError, match="state changed"):
        p.apply(plan, approved_plan_id=plan.plan_id)
    assert calls == []
    assert set(store.state()["plugins"]) == {other}


def test_catalog_change_during_download_rejected_inside_store_lock(tmp_path, monkeypatch):
    ref, other = "lab:core@1.0.0", "lab:other@1.0.0"
    blob = archive(ref)
    item = offers({ref: blob})[0]
    store, p = planner(tmp_path, [item])
    plan = p.plan([ref])
    existing = tmp_path / "other.zip"
    existing.write_bytes(archive(other))
    touched = []

    def mutate(url):
        if not touched:
            touched.append(url)
            store.install_zip(existing)

    calls = transport(monkeypatch, {item.url: blob}, on_request=mutate)
    with pytest.raises(PluginStoreError, match="since plan approval"):
        p.apply(plan, approved_plan_id=plan.plan_id)
    assert calls == [item.url]
    assert set(store.state()["plugins"]) == {other}
    assert len(list(store.objects.iterdir())) == 1


def test_installed_artifact_sha_conflict_blocks_plan(tmp_path, monkeypatch):
    ref = "lab:core@1.0.0"
    blob = archive(ref)
    store, _ = planner(tmp_path, [])
    local = tmp_path / "core.zip"
    local.write_bytes(blob)
    store.install_zip(local)
    different = ArtifactOffer(ref, "https://plugins.example/alternate.zip", "0" * 64)
    p = PluginInstallationPlanner(store, [different],
                                  allowed_origins=("https://plugins.example",))
    calls = transport(monkeypatch, {different.url: blob})
    with pytest.raises(InstallationPlanError, match="conflicts"):
        p.plan([ref])
    assert calls == []


def test_ambiguous_offer_and_disallowed_origin_block_preview(tmp_path):
    ref, sha = "lab:core@1.0.0", "1" * 64
    store = LocalPluginStore(tmp_path / "store")
    with pytest.raises(acquisition.PluginAcquisitionError, match="origin"):
        PluginInstallationPlanner(
            store, [ArtifactOffer(ref, "https://evil.example/archive.zip", sha)],
            allowed_origins=("https://plugins.example",),
        )
    with pytest.raises(InstallationPlanError, match="ambiguous"):
        PluginInstallationPlanner(
            store, [
                ArtifactOffer(ref, "https://plugins.example/a.zip", sha),
                ArtifactOffer(ref, "https://plugins.example/b.zip", sha),
            ],
            allowed_origins=("https://plugins.example",),
        )
