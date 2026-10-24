"""Pinned Plugin Catalog v1 is inert data consumed by the existing planner."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from invariant.load import acquisition
from invariant.load.plugin_catalog import PluginCatalogError, VerifiedPluginCatalog
from invariant.load.plugin_store import LocalPluginStore
from invariant.load.installation_plan import InstallationPlanError


CORE = """from invariant.spec import BasePlugin
class Core(BasePlugin):
    def initialize(self, **deps): self.calls = 0
    def shutdown(self): pass
    def call(self):
        self.calls += 1
        return self.calls
"""
VOICE = """from invariant.spec import BasePlugin
class Voice(BasePlugin):
    def initialize(self, *, core, **deps): self.core = core
    def shutdown(self): pass
    def call(self): return self.core.call()
"""


def package(ref, *, dependencies=()):
    name, version = ref.rsplit("@", 1)
    manifest = {
        "manifest_version": 1,
        "id": name,
        "version": version,
        "source": {
            "code_path": "plugin.py",
            "class_name": "Voice" if dependencies else "Core",
        },
        "dependencies": [
            {"id": dep, "param": "core", "scope": "SINGLETON"}
            for dep in dependencies
        ],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest))
        z.writestr("plugin.py", VOICE if dependencies else CORE)
    return buf.getvalue()


def artifact(ref, sha="a" * 64, *, dependencies=(), url=None):
    return {
        "ref": ref,
        "url": url or "https://plugins.example/" + ref.split("@")[0].split(":")[-1] + ".zip",
        "sha256": sha,
        "dependencies": list(dependencies),
    }


def parse(doc):
    data = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return VerifiedPluginCatalog.from_bytes(
        data, expected_sha256=hashlib.sha256(data).hexdigest(),
    )


def test_catalog_parses_pinned_sorted_immutable_offers():
    value = parse({
        "schema_version": 1,
        "artifacts": [
            artifact("lab:z@1.0.0"),
            artifact("lab:a@1.0", dependencies=("lab:z@1.0.0",)),
        ],
    })
    assert tuple(x.ref for x in value.offers) == ("lab:a@1.0.0", "lab:z@1.0.0")
    assert value.offers[0].dependencies == ("lab:z@1.0.0",)
    with pytest.raises((AttributeError, TypeError)):
        value.offers += ()
    with pytest.raises((AttributeError, TypeError)):
        value.offers[0].ref = "malicious"
    assert len(value.sha256) == 64


def test_different_bytes_cannot_use_old_trusted_digest():
    a = b'{"schema_version":1,"artifacts":[]}'
    expected = hashlib.sha256(a).hexdigest()
    assert VerifiedPluginCatalog.from_bytes(a, expected_sha256=expected).offers == ()
    changed = a.replace(b'[]', b'[{}]')
    with pytest.raises(PluginCatalogError, match="SHA-256 mismatch"):
        VerifiedPluginCatalog.from_bytes(changed, expected_sha256=expected)


@pytest.mark.parametrize("expected", ["", "0" * 63, "Z" * 64, None, 42])
def test_expected_catalog_digest_must_be_64_hex(expected):
    with pytest.raises(PluginCatalogError, match="trusted catalog SHA-256"):
        VerifiedPluginCatalog.from_bytes(
            b'{"schema_version":1,"artifacts":[]}', expected_sha256=expected,
        )


@pytest.mark.parametrize("raw,reason", [
    (b'{"schema_version":1,"schema_version":1,"artifacts":[]}', "duplicate JSON property"),
    (b'{"schema_version":1,"artifacts":[{"ref":"x","ref":"y"}]}', "duplicate JSON property"),
    (b'{"schema_version":true,"artifacts":[]}', "schema_version"),
    (b'{"schema_version":2,"artifacts":[]}', "schema_version"),
    (b'{"schema_version":1,"artifacts":[],"created":"now"}', "requires only"),
    (b'{"schema_version":1,"artifacts":[],"note":NaN}', "invalid JSON"),
    (b'{"schema_version":1,"artifacts":{}}', "artifacts must"),
    (b'{"schema_version":1,"artifacts":[]}\xff', "invalid catalog JSON"),
    (b'', "1..1048576 bytes"),
])
def test_strict_json_and_schema_reject_ambiguity(raw, reason):
    with pytest.raises(PluginCatalogError, match=reason):
        VerifiedPluginCatalog.from_bytes(
            raw, expected_sha256=hashlib.sha256(raw).hexdigest(),
        )


def test_catalog_size_limit_fails_before_json_parsing():
    data = b" " * (1024 * 1024 + 1)
    with pytest.raises(PluginCatalogError, match="1..1048576"):
        VerifiedPluginCatalog.from_bytes(
            data, expected_sha256=hashlib.sha256(data).hexdigest(),
        )


@pytest.mark.parametrize("items,reason", [
    ([artifact("lab:core@1.0"), artifact("lab:core@1.0.0")], "duplicate canonical"),
    ([dict(artifact("lab:core@1.0"), extra="x")], "requires ref"),
    ([dict(artifact("lab:core@1.0"), dependencies=["lab:core@2.0", "lab:core@2.0.0"])], "duplicate required"),
    ([dict(artifact("lab:core@1.0"), dependencies="lab:other@1.0")], "invalid or oversized"),
    ([dict(artifact("lab:core@1.0"), sha256="bad")], "invalid artifact offer"),
    ([dict(artifact("lab:core@1.0"), dependencies=["unversioned"])], "invalid artifact offer"),
])
def test_invalid_or_ambiguous_offers_are_rejected(items, reason):
    with pytest.raises(PluginCatalogError, match=reason):
        parse({"schema_version": 1, "artifacts": items})


def test_catalog_artifact_and_dependencies_are_bounded():
    with pytest.raises(PluginCatalogError, match="at most 256"):
        parse({"schema_version": 1, "artifacts": [
            artifact(f"lab:p{i}@1.0.0") for i in range(257)
        ]})
    with pytest.raises(PluginCatalogError, match="invalid or oversized"):
        parse({"schema_version": 1, "artifacts": [
            artifact("lab:root@1.0.0", dependencies=[
                f"lab:dep{i}@1.0.0" for i in range(65)
            ])
        ]})


def test_catalog_builds_planner_without_network(tmp_path, monkeypatch):
    ref = "lab:core@1.0.0"
    pack = package(ref)
    cat = parse({"schema_version": 1, "artifacts": [
        artifact(ref, hashlib.sha256(pack).hexdigest())
    ]})
    store = LocalPluginStore(tmp_path / "store")

    def no_network(*args, **kwargs):
        pytest.fail("catalog parsing and plan preview cannot contact network")

    monkeypatch.setattr(acquisition.urllib.request, "build_opener", no_network)
    planner = cat.planner(store, allowed_origins={"https://plugins.example"})
    plan = planner.plan([ref])
    assert plan.installed == ()
    assert len(plan.downloads) == 1
    with pytest.raises(InstallationPlanError, match="approval"):
        planner.apply(plan, approved_plan_id="different")
    assert store.state()["plugins"] == {}


def test_host_origin_policy_rejects_catalog_url(tmp_path):
    cat = parse({"schema_version": 1, "artifacts": [
        artifact("lab:core@1.0.0", url="https://unapproved.example/core.zip")
    ]})
    with pytest.raises(acquisition.PluginAcquisitionError, match="origin not allowed"):
        cat.planner(
            LocalPluginStore(tmp_path / "store"),
            allowed_origins={"https://plugins.example"},
        )


def test_verified_catalog_feeds_shared_dep_plan_and_atomic_install(tmp_path, monkeypatch):
    core, a, b = "lab:core@1.0.0", "lab:voice-a@1.0.0", "lab:voice-b@1.0.0"
    blobs = {core: package(core),
             a: package(a, dependencies=(core,)),
             b: package(b, dependencies=(core,))}
    entries = [
        artifact(ref, hashlib.sha256(blob).hexdigest(),
                 dependencies=(core,) if ref != core else ())
        for ref, blob in blobs.items()
    ]
    cat = parse({"schema_version": 1, "artifacts": entries})
    by_url = {entry["url"]: blobs[entry["ref"]] for entry in entries}
    calls = []

    class Response(io.BytesIO):
        status = 200
        headers = {"Content-Encoding": "identity"}

        def __init__(self, url, data):
            super().__init__(data)
            self.url = url

        def geturl(self):
            return self.url

    class Opener:
        def open(self, req, timeout):
            calls.append(req.full_url)
            return Response(req.full_url, by_url[req.full_url])

    monkeypatch.setattr(acquisition.urllib.request, "build_opener", lambda *a: Opener())
    store = LocalPluginStore(tmp_path / "store")
    planner = cat.planner(store, allowed_origins={"https://plugins.example"})
    plan = planner.plan([a, b])
    assert [x.ref for x in plan.downloads] == [core, a, b]
    assert calls == []
    result = planner.apply(plan, approved_plan_id=plan.plan_id)
    assert set(result["new_objects"]) == {core, a, b}
    assert len(calls) == 3
    assert len(list(store.objects.iterdir())) == 3
    with store.open_service() as service:
        first = service.get(a)
        second = service.get(b)
        assert first.call() == 1
        assert second.call() == 2
        assert first.core is second.core
    second_plan = planner.plan([a, b])
    assert second_plan.downloads == ()
    assert set(second_plan.installed) == {core, a, b}
    assert len(calls) == 3


def test_catalog_offers_cannot_bypass_downloaded_manifest_dependencies(tmp_path, monkeypatch):
    core, voice = "lab:core@1.0.0", "lab:voice@1.0.0"
    blob = package(voice, dependencies=(core,))
    entry = artifact(voice, hashlib.sha256(blob).hexdigest(), dependencies=())
    cat = parse({"schema_version": 1, "artifacts": [entry]})
    class Resp(io.BytesIO):
        status = 200
        headers = {"Content-Encoding": "identity"}
        def geturl(self): return entry["url"]

    class Opener:
        def open(self, req, timeout): return Resp(blob)

    monkeypatch.setattr(acquisition.urllib.request, "build_opener", lambda *a: Opener())
    store = LocalPluginStore(tmp_path / "store")
    p = cat.planner(store, allowed_origins={"https://plugins.example"})
    plan = p.plan([voice])
    with pytest.raises(InstallationPlanError, match="dependencies differ"):
        p.apply(plan, approved_plan_id=plan.plan_id)
    assert store.state()["plugins"] == {}
