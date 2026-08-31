from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from invariant.api import PluginService


_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE_DIR = _REPO_ROOT / "examples" / "hello_plugin"


def test_manifest_first_service_runs_checked_hello_plugin() -> None:
    service = PluginService.from_manifest_paths([_EXAMPLE_DIR])
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
        [sys.executable, str(_EXAMPLE_DIR / "run.py")],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "Hello, Invariant!"
