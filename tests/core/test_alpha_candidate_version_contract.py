"""Release candidate metadata must agree with shipped and published surfaces.

Prevents an alpha wheel with one version in pyproject.toml and another
version in the public README, release notes, or imported distribution.
"""
from __future__ import annotations

import tomllib
from importlib.metadata import distribution
from pathlib import Path

import invariant


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_ALPHA = "0.1.1a1"


def test_candidate_version_matches_installed_distribution_and_readme() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]
    assert project["version"] == EXPECTED_ALPHA
    assert distribution("invariant-runtime").version == EXPECTED_ALPHA
    assert invariant.__version__ == EXPECTED_ALPHA
    assert project["requires-python"] == ">=3.11"
    assert project["dependencies"] == []

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "badge/0.1.1a1%20alpha" in readme
    assert "This branch stages 0.1.1a1 as an unpublished alpha candidate" in readme

    notes = (
        ROOT / "docs" / "alpha-candidate-0.1.1a1.md"
    ).read_text(encoding="utf-8")
    assert "NOT TAGGED, RELEASED, OR PUBLISHED" in notes
    assert "0.1.1a1" in notes
    assert "invariant:capability.process@0.2.0" in notes


def test_isolated_wheel_smoke_script_remains_valid_python() -> None:
    script = ROOT / "tools" / "verify_installed_alpha_candidate.py"
    source = script.read_text(encoding="utf-8")
    compile(source, str(script), "exec")
    assert 'EXPECTED_VERSION = "0.1.1a1"' in source
    assert "INVARIANT_ALPHA_INSTALLED_WHEEL_PASS" in source
    assert "from invariant.api import PluginService" in source
