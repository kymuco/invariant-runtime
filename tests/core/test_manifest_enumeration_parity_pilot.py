"""Observational contract tests for a non-production manifest enumeration pilot."""

from __future__ import annotations

import importlib
import runpy
import subprocess
import sys
from pathlib import Path

import pytest


def _pilot():
    return runpy.run_path(
        str(
            Path(__file__).resolve().parents[2]
            / "examples"
            / "profile_manifest_enumeration.py"
        )
    )


def _manifest(folder: Path, name: str = "manifest.json") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    manifest = folder / name
    manifest.write_text('{"manifest_version":1}\n', encoding="utf-8")
    return manifest


def test_enumeration_candidate_preserves_nested_manifest_sequence(
    tmp_path: Path,
) -> None:
    pilot = _pilot()
    root = tmp_path / "root"
    expected = [
        _manifest(root / "a"),
        _manifest(root / "a" / "child"),
        _manifest(root / ".hidden"),
        _manifest(root / "b"),
    ]
    (root / "a" / "unrelated.txt").write_text("ignored", encoding="utf-8")
    parity = pilot["_compare"]([root])

    assert parity["eligible"] is True
    assert parity["same_multiset"] is True
    assert parity["baseline_count"] == 4
    assert set(parity["baseline_paths"]) == {str(p) for p in expected}
    assert parity["baseline_paths"] == parity["candidate_paths"]


def test_enumeration_candidate_preserves_duplicate_search_roots(
    tmp_path: Path,
) -> None:
    pilot = _pilot()
    root = tmp_path / "plugins"
    manifest = _manifest(root / "nested")

    parity = pilot["_compare"]([root, root])

    assert parity["eligible"] is True
    assert parity["baseline_paths"] == [str(manifest), str(manifest)]
    assert parity["candidate_paths"] == [str(manifest), str(manifest)]


def test_enumeration_candidate_skips_missing_roots(
    tmp_path: Path,
) -> None:
    pilot = _pilot()
    root = tmp_path / "present"
    one = _manifest(root)
    missing = tmp_path / "absent"

    parity = pilot["_compare"]([missing, root])

    assert parity["eligible"] is True
    assert parity["baseline_paths"] == [str(one)]


def test_enumeration_candidate_detects_discovery_between_runs(
    tmp_path: Path,
) -> None:
    pilot = _pilot()
    root = tmp_path / "pack"
    first = _manifest(root / "one")
    before = pilot["_compare"]([root])
    second = _manifest(root / "two")
    after = pilot["_compare"]([root])

    assert before["eligible"] is True
    assert after["eligible"] is True
    assert set(after["baseline_paths"]) == {str(first), str(second)}


def test_enumeration_comparison_rejects_order_changes_without_mutating_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pilot = _pilot()
    root = tmp_path / "pack"
    _manifest(root / "a")
    _manifest(root / "b")
    baseline = pilot["_baseline"]([root])

    monkeypatch.setitem(
        pilot["_compare"].__globals__,
        "_candidate",
        lambda paths: list(reversed(pilot["_baseline"](paths))),
    )
    compare = pilot["_compare"]([root])

    assert compare["eligible"] is False
    assert compare["same_order_and_paths"] is False
    assert compare["same_multiset"] is True
    assert compare["first_differences"]
    assert len(baseline) == 2


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junction-specific")
def test_enumeration_candidate_records_windows_junction_parity(
    tmp_path: Path,
) -> None:
    pilot = _pilot()
    root = tmp_path / "pack"
    root.mkdir()
    outside = tmp_path / "target"
    external = _manifest(outside)
    link = root / "junction"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if created.returncode != 0:
        pytest.skip("Windows directory junction creation unavailable")

    try:
        parity = pilot["_compare"]([root])
        assert parity["eligible"] == (
            parity["baseline_paths"] == parity["candidate_paths"]
        )
        assert parity["same_multiset"] == (
            sorted(parity["baseline_paths"]) == sorted(parity["candidate_paths"])
        )
    finally:
        link.rmdir()
    assert external.is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows glob case matching")
def test_enumeration_candidate_records_windows_case_parity(
    tmp_path: Path,
) -> None:
    pilot = _pilot()
    root = tmp_path / "pack"
    manifest = _manifest(root, name="MANIFEST.JSON")
    parity = pilot["_compare"]([root])

    # On the observed Windows/Python 3.11 host, the current rglob pattern
    # does not discover uppercase MANIFEST.JSON, whereas normcase-based
    # os.walk matching does. This is a proven candidate parity failure.
    assert parity["eligible"] is False
    assert parity["same_multiset"] is False
    assert parity["baseline_paths"] == []
    assert parity["candidate_paths"] == [str(manifest)]
    assert parity["first_differences"] == [
        {"position": 0, "baseline": None, "candidate": str(manifest)}
    ]


@pytest.mark.parametrize("count", [0, 4, 121])
def test_enumeration_rejects_invalid_counts(count: int) -> None:
    with pytest.raises(ValueError, match="between 5 and 120"):
        _pilot()["_validate_samples"](count)


def test_enumeration_rejects_boolean_sample_count() -> None:
    with pytest.raises(TypeError, match="must be an integer"):
        _pilot()["_validate_samples"](True)


def test_enumeration_refuses_existing_proof_root(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    existing = output / "keep.txt"
    existing.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="must be new"):
        _pilot()["run"](output, 5)

    assert existing.read_text(encoding="utf-8") == "keep"


def test_enumeration_has_no_production_loader_patch() -> None:
    module = importlib.import_module("invariant.load.sources.manifest")
    before = module.ManifestSource.discover
    pilot = _pilot()
    root = Path(__file__).resolve().parents[2] / "examples"

    pilot["_compare"]([root])

    assert module.ManifestSource.discover is before
