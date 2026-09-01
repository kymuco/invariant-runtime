from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from invariant.api import (
    CapabilityUnavailableError,
    EphemeralPolicy,
    PluginService,
    SandboxLimits,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_HELLO_EXAMPLE_DIR = _REPO_ROOT / "examples" / "hello_plugin"
_CAPABILITY_NOTES_DIR = _REPO_ROOT / "examples" / "capability_notes"


def test_manifest_first_service_runs_checked_hello_plugin() -> None:
    service = PluginService.from_manifest_paths([_HELLO_EXAMPLE_DIR])
    try:
        hello = service.get("example:hello")
        assert hello.greet("Invariant") == "Hello, Invariant!"
        assert hello.meta.id == "example:hello"
        assert hello.meta.version == "1.0.0"
    finally:
        service.close()


def test_manifest_first_service_requires_search_path() -> None:
    with pytest.raises(ValueError, match="search_paths"):
        PluginService.from_manifest_paths([])


def test_checked_hello_example_runs_as_documented() -> None:
    proc = subprocess.run(
        [sys.executable, str(_HELLO_EXAMPLE_DIR / "run.py")],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "Hello, Invariant!"


def test_dependency_and_capability_graph_runs_under_granted_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    service = PluginService.from_manifest_paths([_CAPABILITY_NOTES_DIR])
    policy = EphemeralPolicy(limits=SandboxLimits(allow_fs_write=True))

    try:
        with service.execution(policy=policy) as execution:
            notes = execution.get("example:notes")
            notes.save("governed dependency")
            assert notes.load() == "governed dependency"
            assert notes.meta.id == "example:notes"
            assert notes.meta.version == "1.0.0"
    finally:
        service.close()


def test_policy_denial_blocks_required_capability_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    service = PluginService.from_manifest_paths([_CAPABILITY_NOTES_DIR])
    policy = EphemeralPolicy(limits=SandboxLimits(allow_fs=False))

    try:
        with service.execution(policy=policy) as execution:
            with pytest.raises(CapabilityUnavailableError, match="fs_sandbox"):
                execution.get("example:notes")
    finally:
        service.close()


def test_checked_capability_notes_example_runs_as_documented(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(_CAPABILITY_NOTES_DIR / "run.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "Notes: governed dependency"


def test_capability_notes_example_stays_on_public_import_surfaces() -> None:
    storage_plugin = (_CAPABILITY_NOTES_DIR / "storage" / "plugin.py").read_text(encoding="utf-8")
    notes_plugin = (_CAPABILITY_NOTES_DIR / "notes" / "plugin.py").read_text(encoding="utf-8")
    runner = (_CAPABILITY_NOTES_DIR / "run.py").read_text(encoding="utf-8")

    assert "from invariant.spec import BasePlugin, FilesystemCapability" in storage_plugin
    assert "fs: FilesystemCapability" in storage_plugin

    for plugin_source in (storage_plugin, notes_plugin):
        assert "from invariant.spec import BasePlugin" in plugin_source
        assert "invariant.exec" not in plugin_source
        assert "invariant.load" not in plugin_source
        assert "invariant.catalog" not in plugin_source

    assert "from invariant.api import EphemeralPolicy, PluginService, SandboxLimits" in runner
    assert "invariant.exec" not in runner
    assert "invariant.load" not in runner
    assert "invariant.catalog" not in runner
