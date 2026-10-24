"""Explicit HTTPS acquisition: verify before local PluginStore installation."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from invariant.load import acquisition as acq


class Response(io.BytesIO):
    def __init__(self, payload, url, *, length=None, encoding=None, status=200):
        super().__init__(payload)
        self._url = url
        self.status = status
        self.headers = {}
        if length is not None:
            self.headers["Content-Length"] = str(length)
        if encoding is not None:
            self.headers["Content-Encoding"] = encoding

    def geturl(self):
        return self._url


class FakeOpener:
    def __init__(self, make_response):
        self.make_response = make_response
        self.calls = 0

    def open(self, request, timeout):
        self.calls += 1
        return self.make_response(request.full_url)


def archive_bytes():
    sink = io.BytesIO()
    with zipfile.ZipFile(sink, "w") as out:
        out.writestr("manifest.json", json.dumps({
            "manifest_version": 1, "id": "lab:core", "version": "1.0.0",
            "source": {"code_path": "plugin.py", "class_name": "Core"},
        }))
        out.writestr("plugin.py", "class Core: pass")
    return sink.getvalue()


def set_response(monkeypatch, data, *, options=None):
    options = options or {}
    fake = FakeOpener(lambda url: Response(data, url, **options))
    monkeypatch.setattr(acq.urllib.request, "build_opener", lambda *handlers: fake)
    return fake


def test_verified_download_exists_only_during_explicit_context(monkeypatch):
    data = archive_bytes()
    opener = set_response(monkeypatch, data)
    with acq.download_verified_zip(
        "https://plugins.example/core.zip",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        allowed_origins=["https://plugins.example"],
    ) as local:
        assert local.read_bytes() == data
        path = local
    assert not path.exists()
    assert opener.calls == 1


def test_mismatched_sha256_never_yields_zip(monkeypatch):
    set_response(monkeypatch, archive_bytes())
    with pytest.raises(acq.PluginAcquisitionError, match="SHA-256 mismatch"):
        with acq.download_verified_zip(
            "https://plugins.example/core.zip",
            expected_sha256="0" * 64,
            allowed_origins=["https://plugins.example"],
        ):
            pytest.fail("unverified bytes must not reach the installer")


@pytest.mark.parametrize("url,allowed", [
    ("http://plugins.example/core.zip", ["https://plugins.example"]),
    ("https://other.example/core.zip", ["https://plugins.example"]),
    ("https://u:p@plugins.example/core.zip", ["https://plugins.example"]),
    ("https://plugins.example\\@other.example/core.zip", ["https://plugins.example"]),
    ("https://plugins.example/core.zip#part", ["https://plugins.example"]),
    ("https://plugins.example:8443/core.zip", ["https://plugins.example"]),
    ("https://plugins.example:0/core.zip", ["https://plugins.example"]),
    ("https://plugins.example/core.zip", ["https://plugins.example/path"]),
    ("https://plugins.example/core.zip", []),
])
def test_disallowed_addresses_never_connect(monkeypatch, url, allowed):
    opener = set_response(monkeypatch, archive_bytes())
    with pytest.raises(acq.PluginAcquisitionError):
        with acq.download_verified_zip(
            url, expected_sha256="a" * 64, allowed_origins=allowed,
        ):
            pytest.fail("disallowed request must not be executed")
    assert opener.calls == 0


@pytest.mark.parametrize("options,error", [
    ({"length": 1_000_000}, "download size limit"),
    ({"length": "garbage"}, "content length"),
    ({"encoding": "gzip"}, "compressed HTTP"),
    ({"status": 301}, "redirect"),
])
def test_bad_response_fails_closed(monkeypatch, options, error):
    data = archive_bytes()
    set_response(monkeypatch, data, options=options)
    with pytest.raises(acq.PluginAcquisitionError, match=error):
        with acq.download_verified_zip(
            "https://plugins.example/core.zip",
            expected_sha256=hashlib.sha256(data).hexdigest(),
            allowed_origins=["https://plugins.example"],
            max_bytes=len(data),
        ):
            pytest.fail("invalid response must not be yielded")


def test_stream_size_limit_without_content_length(monkeypatch):
    data = archive_bytes()
    set_response(monkeypatch, data)
    with pytest.raises(acq.PluginAcquisitionError, match="size limit"):
        with acq.download_verified_zip(
            "https://plugins.example/core.zip",
            expected_sha256=hashlib.sha256(data).hexdigest(),
            allowed_origins=["https://plugins.example"],
            max_bytes=len(data) - 1,
        ):
            pytest.fail("oversized body must not be yielded")


def test_non_zip_rejected_even_with_matching_hash(monkeypatch):
    data = b"not-an-archive"
    set_response(monkeypatch, data)
    with pytest.raises(acq.PluginAcquisitionError, match="not a ZIP"):
        with acq.download_verified_zip(
            "https://plugins.example/core.zip",
            expected_sha256=hashlib.sha256(data).hexdigest(),
            allowed_origins=["https://plugins.example"],
        ):
            pytest.fail("invalid archive must not be yielded")


def test_urllib_redirect_handler_denies_redirect():
    assert acq._NoRedirect().redirect_request(
        None, None, 302, "redirect", {}, "https://other.example/core.zip",
    ) is None


def _plugin_zip(plugin_id, *, dependencies=()):
    code = """from invariant.spec import BasePlugin
class Example(BasePlugin):
    def initialize(self, *, core=None, **deps):
        self.core = core
        self.count = 0
    def shutdown(self): pass
    def call(self):
        if self.core is not None: return self.core.call()
        self.count += 1
        return self.count
"""
    manifest = {
        "manifest_version": 1, "id": plugin_id, "version": "1.0.0",
        "source": {"code_path": "plugin.py", "class_name": "Example"},
    }
    if dependencies:
        manifest["dependencies"] = [
            {"id": dep, "param": "core", "scope": "SINGLETON"}
            for dep in dependencies
        ]
    sink = io.BytesIO()
    with zipfile.ZipFile(sink, "w") as out:
        out.writestr("manifest.json", json.dumps(manifest))
        out.writestr("plugin.py", code)
    return sink.getvalue()


def test_https_acquisition_reuses_single_installed_plugin_dependency(tmp_path, monkeypatch):
    from invariant.load.plugin_store import LocalPluginStore

    origin = "https://plugins.example"
    payloads = {
        origin + "/core.zip": _plugin_zip("lab:core"),
        origin + "/a.zip": _plugin_zip(
            "lab:voice-a", dependencies=["lab:core@1.0.0"],
        ),
        origin + "/b.zip": _plugin_zip(
            "lab:voice-b", dependencies=["lab:core@1.0.0"],
        ),
    }
    requests = []

    class MappingOpener:
        def open(self, req, timeout):
            requests.append(req.full_url)
            payload = payloads[req.full_url]
            return Response(payload, req.full_url, length=len(payload))

    monkeypatch.setattr(
        acq.urllib.request, "build_opener", lambda *handlers: MappingOpener(),
    )
    store = LocalPluginStore(tmp_path / "store")
    for filename in ("core.zip", "a.zip", "b.zip"):
        url = origin + "/" + filename
        expected = hashlib.sha256(payloads[url]).hexdigest()
        with acq.download_verified_zip(
            url, expected_sha256=expected, allowed_origins=[origin],
        ) as archive:
            store.install_zip(archive, expected_sha256=expected)

    assert len(list(store.objects.iterdir())) == 3
    with store.open_service() as service:
        a = service.get("lab:voice-a@1.0.0")
        b = service.get("lab:voice-b@1.0.0")
        assert a.call() == 1
        assert b.call() == 2
        assert a.core is b.core
    assert len(requests) == 3

    alias = origin + "/alternate-core.zip"
    payloads[alias] = payloads[origin + "/core.zip"]
    expected = hashlib.sha256(payloads[alias]).hexdigest()
    with acq.download_verified_zip(
        alias, expected_sha256=expected, allowed_origins=[origin],
    ) as archive:
        assert store.install_zip(archive, expected_sha256=expected)["new_objects"] == []
    assert len(list(store.objects.iterdir())) == 3


def test_http_error_response_closes_without_reading_error_body(monkeypatch):
    response = io.BytesIO(b"untrusted error payload")
    url = "https://plugins.example/core.zip"

    class ErrorOpener:
        def open(self, request, timeout):
            raise acq.urllib.error.HTTPError(
                url, 302, "redirect", {}, response,
            )

    monkeypatch.setattr(acq.urllib.request, "build_opener", lambda *args: ErrorOpener())
    with pytest.raises(acq.PluginAcquisitionError, match="download rejected"):
        with acq.download_verified_zip(
            url, expected_sha256="a" * 64,
            allowed_origins=["https://plugins.example"],
        ):
            pytest.fail("HTTPError must not yield a ZIP")
    assert response.closed


def test_verified_installed_plugin_skips_network_and_returns_reused(tmp_path, monkeypatch):
    from invariant.load.plugin_store import LocalPluginStore

    data = _plugin_zip("lab:core")
    url = "https://plugins.example/core.zip"
    fake = set_response(monkeypatch, data)
    store = LocalPluginStore(tmp_path / "store")
    request = dict(
        store=store, ref="lab:core@1.0.0", url=url,
        expected_sha256=hashlib.sha256(data).hexdigest(),
        allowed_origins=["https://plugins.example"],
    )
    first = acq.install_verified_https_plugin(**request)
    second = acq.install_verified_https_plugin(**request)
    assert first["reused"] is False
    assert second["reused"] is True
    assert fake.calls == 1
    assert len(list(store.objects.iterdir())) == 1
    with store.open_service() as service:
        assert service.get("lab:core@1.0.0").call() == 1


def test_installed_dependency_can_be_promoted_without_redownload(tmp_path, monkeypatch):
    from invariant.load.plugin_store import LocalPluginStore

    store = LocalPluginStore(tmp_path / "store")
    core = _plugin_zip("lab:core")
    voice = _plugin_zip("lab:voice", dependencies=["lab:core@1.0.0"])
    c = tmp_path / "core.zip"
    v = tmp_path / "voice.zip"
    c.write_bytes(core)
    v.write_bytes(voice)
    store.install([c, v], roots=["lab:voice@1.0.0"])
    assert store.state()["roots"] == ["lab:voice@1.0.0"]
    fake = set_response(monkeypatch, core)
    result = acq.install_verified_https_plugin(
        store, "lab:core@1.0.0", "https://plugins.example/core.zip",
        expected_sha256=hashlib.sha256(core).hexdigest(),
        allowed_origins=["https://plugins.example"],
    )
    assert result["reused"] is True
    assert fake.calls == 0
    assert set(store.state()["roots"]) == {"lab:core@1.0.0", "lab:voice@1.0.0"}


def test_cached_hit_still_rejects_disallowed_origin(tmp_path, monkeypatch):
    from invariant.load.plugin_store import LocalPluginStore

    data = _plugin_zip("lab:core")
    store = LocalPluginStore(tmp_path / "store")
    archive = tmp_path / "core.zip"
    archive.write_bytes(data)
    store.install_zip(archive)
    fake = set_response(monkeypatch, data)
    with pytest.raises(acq.PluginAcquisitionError, match="origin not allowed"):
        acq.install_verified_https_plugin(
            store, "lab:core@1.0.0", "https://evil.example/core.zip",
            expected_sha256=hashlib.sha256(data).hexdigest(),
            allowed_origins=["https://plugins.example"],
        )
    assert fake.calls == 0


def test_different_expected_archive_digest_cannot_skip_network(tmp_path, monkeypatch):
    from invariant.load.plugin_store import LocalPluginStore

    data = _plugin_zip("lab:core")
    store = LocalPluginStore(tmp_path / "store")
    archive = tmp_path / "core.zip"
    archive.write_bytes(data)
    store.install_zip(archive)
    # Valid ZIP, but different contents and identical plugin ID/version.
    repacked = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as original:
        with zipfile.ZipFile(repacked, "w") as changed:
            for name in original.namelist():
                payload = original.read(name)
                if name == "plugin.py":
                    payload = payload.replace(b"class Example", b"class Changed")
                changed.writestr(name, payload)
    different = repacked.getvalue()
    fake = set_response(monkeypatch, different)
    with pytest.raises(Exception, match="collision"):
        acq.install_verified_https_plugin(
            store, "lab:core@1.0.0", "https://plugins.example/core.zip",
            expected_sha256=hashlib.sha256(different).hexdigest(),
            allowed_origins=["https://plugins.example"],
        )
    assert fake.calls == 1
    assert len(list(store.objects.iterdir())) == 1


def test_downloaded_plugin_identity_must_match_requested_ref(tmp_path, monkeypatch):
    from invariant.load.plugin_store import LocalPluginStore, PluginStoreError

    data = _plugin_zip("lab:other")
    store = LocalPluginStore(tmp_path / "store")
    fake = set_response(monkeypatch, data)
    with pytest.raises(PluginStoreError, match="identity"):
        acq.install_verified_https_plugin(
            store, "lab:core@1.0.0", "https://plugins.example/archive.zip",
            expected_sha256=hashlib.sha256(data).hexdigest(),
            allowed_origins=["https://plugins.example"],
        )
    assert fake.calls == 1
    assert store.state()["plugins"] == {}


def test_modified_installed_artifact_fails_closed_before_network(tmp_path, monkeypatch):
    from invariant.load.plugin_store import LocalPluginStore, PluginStoreError

    data = _plugin_zip("lab:core")
    store = LocalPluginStore(tmp_path / "store")
    archive = tmp_path / "core.zip"
    archive.write_bytes(data)
    store.install_zip(archive)
    target = store.plugin_roots()[0] / "plugin.py"
    target.chmod(0o644)
    target.write_text("# corrupted", encoding="utf-8")
    fake = set_response(monkeypatch, data)
    with pytest.raises(PluginStoreError, match="modified"):
        acq.install_verified_https_plugin(
            store, "lab:core@1.0.0", "https://plugins.example/core.zip",
            expected_sha256=hashlib.sha256(data).hexdigest(),
            allowed_origins=["https://plugins.example"],
        )
    assert fake.calls == 0


def test_two_concurrent_requests_with_same_digest_download_once(tmp_path, monkeypatch):
    import concurrent.futures
    import threading
    from invariant.load.plugin_store import LocalPluginStore

    data = _plugin_zip("lab:core")
    store = LocalPluginStore(tmp_path / "store")
    url = "https://plugins.example/core.zip"
    counter = {"calls": 0}
    mutex = threading.Lock()

    class CountingOpener:
        def open(self, request, timeout):
            with mutex:
                counter["calls"] += 1
            return Response(data, request.full_url, length=len(data))

    monkeypatch.setattr(acq.urllib.request, "build_opener", lambda *handlers: CountingOpener())
    request = dict(
        store=store, ref="lab:core@1.0.0", url=url,
        expected_sha256=hashlib.sha256(data).hexdigest(),
        allowed_origins=["https://plugins.example"],
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(acq.install_verified_https_plugin, **request) for _ in range(2)]
        outcomes = [future.result(timeout=30) for future in futures]
    assert counter["calls"] == 1
    assert sorted(result["reused"] for result in outcomes) == [False, True]
    assert len(list(store.objects.iterdir())) == 1


def test_catalog_wrong_plugin_identity_rejected_before_cache_hit(tmp_path, monkeypatch):
    from invariant.load.plugin_store import LocalPluginStore, PluginStoreError

    data = _plugin_zip("lab:other")
    store = LocalPluginStore(tmp_path / "store")
    archive = tmp_path / "other.zip"
    archive.write_bytes(data)
    store.install_zip(archive)
    old = store.state()
    only_digest = old["plugins"]["lab:other@1.0.0"]
    # The object and receipt are intact; only the catalog identity is wrong.
    forged = {
        "schema": 1,
        "roots": ["lab:core@1.0.0"],
        "plugins": {"lab:core@1.0.0": only_digest},
    }
    store.catalog.write_text(json.dumps(forged), encoding="utf-8")
    fake = set_response(monkeypatch, data)
    with pytest.raises(PluginStoreError, match="catalog plugin identity mismatch"):
        acq.install_verified_https_plugin(
            store, "lab:core@1.0.0", "https://plugins.example/core.zip",
            expected_sha256=hashlib.sha256(data).hexdigest(),
            allowed_origins=["https://plugins.example"],
        )
    assert fake.calls == 0
    assert store.state() == forged
