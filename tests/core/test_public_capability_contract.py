from __future__ import annotations

from pathlib import Path

import pytest

import invariant.spec as spec
from invariant.exec.caps.catalog import CATALOG
from invariant.exec.caps.config_source import EnvDictConfigSource
from invariant.exec.caps.fs import FSAccess
from invariant.exec.caps.http import HttpAccess
from invariant.exec.caps.ids import (
    CAP_AUDIT,
    CAP_CONFIG_SOURCE,
    CAP_FS_SANDBOX,
    CAP_HTTP,
    CAP_INSPECTOR,
    CAP_KV,
    CAP_PREFIX,
    CAP_WORKSPACE,
)
from invariant.exec.caps.inspector import RuntimeInspector
from invariant.exec.caps.kv import KVStore
from invariant.spec import (
    BUILTIN_CAPABILITIES,
    CONFIG_SOURCE_CAPABILITY,
    FS_SANDBOX_CAPABILITY,
    HTTP_CAPABILITY,
    INSPECTOR_CAPABILITY,
    KEY_VALUE_CAPABILITY,
    WORKSPACE_CAPABILITY,
    CapabilityOperationError,
    CapabilityRef,
    ConfigSourceCapability,
    FilesystemCapability,
    FilesystemCapabilityError,
    HttpCapability,
    HttpCapabilityError,
    KeyValueCapability,
    KeyValueCapabilityError,
    RuntimeInspectorCapability,
)
from invariant.spec import capabilities as public_capabilities


_PUBLIC_CAPABILITY_NAMES = {
    "BUILTIN_CAPABILITIES",
    "CONFIG_SOURCE_CAPABILITY",
    "CapabilityOperationError",
    "CapabilityRef",
    "ConfigSourceCapability",
    "FS_SANDBOX_CAPABILITY",
    "FilesystemCapability",
    "FilesystemCapabilityError",
    "HTTP_CAPABILITY",
    "HttpCapability",
    "HttpCapabilityError",
    "INSPECTOR_CAPABILITY",
    "KEY_VALUE_CAPABILITY",
    "KeyValueCapability",
    "KeyValueCapabilityError",
    "RuntimeInspectorCapability",
    "WORKSPACE_CAPABILITY",
}


def test_public_capability_module_exports_are_explicit() -> None:
    assert set(public_capabilities.__all__) == _PUBLIC_CAPABILITY_NAMES
    assert "CAPABILITY_PREFIX" not in public_capabilities.__all__


def test_public_capability_refs_are_canonical_runtime_catalog_contracts() -> None:
    expected = {ref.id: ref.version for ref in BUILTIN_CAPABILITIES}
    runtime_public = {
        cap_id: cap_spec.version
        for cap_id, cap_spec in CATALOG.items()
        if cap_id != CAP_AUDIT
    }

    assert runtime_public == expected
    assert CAP_AUDIT not in expected
    assert FS_SANDBOX_CAPABILITY.ref == "invariant:capability.fs_sandbox@0.3.0"
    assert WORKSPACE_CAPABILITY.ref == "invariant:capability.workspace@0.3.0"
    assert HTTP_CAPABILITY.ref == "invariant:capability.http@0.1.0"
    assert KEY_VALUE_CAPABILITY.ref == "invariant:capability.kv@0.1.0"
    assert CONFIG_SOURCE_CAPABILITY.ref == "invariant:capability.config_source@0.1.0"
    assert INSPECTOR_CAPABILITY.ref == "invariant:capability.inspector@0.1.0"


def test_legacy_runtime_ids_are_shims_over_public_contracts() -> None:
    assert CAP_PREFIX == "invariant:capability."
    assert CAP_FS_SANDBOX == FS_SANDBOX_CAPABILITY.id
    assert CAP_WORKSPACE == WORKSPACE_CAPABILITY.id
    assert CAP_HTTP == HTTP_CAPABILITY.id
    assert CAP_KV == KEY_VALUE_CAPABILITY.id
    assert CAP_CONFIG_SOURCE == CONFIG_SOURCE_CAPABILITY.id
    assert CAP_INSPECTOR == INSPECTOR_CAPABILITY.id


def test_capability_ref_validates_and_normalizes_concrete_versions() -> None:
    ref = CapabilityRef(" invariant:capability.example ", "1.2")
    assert ref.id == "invariant:capability.example"
    assert ref.version == "1.2.0"
    assert ref.ref == "invariant:capability.example@1.2.0"
    assert str(ref) == ref.ref

    with pytest.raises(ValueError, match="Invalid capability id"):
        CapabilityRef("example:bad", "1.0.0")
    with pytest.raises(ValueError, match="Invalid capability id"):
        CapabilityRef("invariant:capability.", "1.0.0")
    with pytest.raises(ValueError, match="without @version"):
        CapabilityRef("invariant:capability.example@1.0.0", "1.0.0")


def test_runtime_implementations_satisfy_public_protocols(tmp_path: Path) -> None:
    fs = FSAccess(root=tmp_path, write=True)
    http = HttpAccess(allow_hosts=())
    kv = KVStore(fs=fs)
    config = EnvDictConfigSource({"example.value": 1})
    inspector = RuntimeInspector(registry=object(), runtime=object())

    assert isinstance(fs, FilesystemCapability)
    assert isinstance(http, HttpCapability)
    assert isinstance(kv, KeyValueCapability)
    assert isinstance(config, ConfigSourceCapability)
    assert isinstance(inspector, RuntimeInspectorCapability)


def test_operation_failures_are_catchable_through_public_spec_errors(tmp_path: Path) -> None:
    assert issubclass(FilesystemCapabilityError, CapabilityOperationError)
    assert issubclass(HttpCapabilityError, CapabilityOperationError)
    assert issubclass(KeyValueCapabilityError, CapabilityOperationError)

    with pytest.raises(FilesystemCapabilityError, match="Write denied"):
        FSAccess(root=tmp_path).write_text("note.txt", "blocked")

    with pytest.raises(HttpCapabilityError, match="allowlist"):
        HttpAccess(allow_hosts=()).get_text("https://example.com")

    fs = FSAccess(root=tmp_path, write=True)
    fs.write_text("broken-kv.json", "not-json")
    kv = KVStore(fs=fs, rel_path="broken-kv.json", repair_on_write=False)
    with pytest.raises(KeyValueCapabilityError, match="not valid JSON"):
        kv.get("missing")


def test_filesystem_protocol_exposes_operations_not_implementation_layout() -> None:
    assert "read_text" in FilesystemCapability.__dict__
    assert "write_text" in FilesystemCapability.__dict__
    assert "mkdir" in FilesystemCapability.__dict__
    assert "resolve" not in FilesystemCapability.__dict__
    assert "root" not in FilesystemCapability.__dict__

    source = (Path(spec.__file__).with_name("capabilities.py")).read_text(encoding="utf-8")
    assert "invariant.exec" not in source
