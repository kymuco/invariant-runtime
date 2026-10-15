"""Keep the private-repo external consumer proof runnable independently.

The Invariant candidate wheel is intentionally pinned to a lifecycle
commit from BEFORE the proof-only Python script existed. This test
prevents accidentally reintroducing the impossible worktree copy.
"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_external_consumer_probe_is_valid_python_source():
    probe = ROOT / "tools" / "runplane_installed_compat_probe.py"
    compile(probe.read_text(encoding="utf-8"), str(probe), "exec")


def test_wrapper_fetches_proof_script_independently_of_pinned_wheel_commit():
    wrapper = (
        ROOT / "tools" / "runplane_installed_wheel_proof.ps1"
    ).read_text(encoding="utf-8")

    assert 'research/runplane-installed-wheel-consumer-2026-10-15' in wrapper
    assert '$ProbeCommit = (& git -C $InvariantRepo rev-parse FETCH_HEAD).Trim()' in wrapper
    assert '$ProbeObject = "${ProbeCommit}:tools/runplane_installed_compat_probe.py"' in wrapper
    assert 'git -C $InvariantRepo show $ProbeObject' in wrapper
    assert '$ProbeSource | Set-Content -LiteralPath $Probe -Encoding UTF8' in wrapper
    assert 'Join-Path $InvariantTree "tools\runplane_installed_compat_probe.py"' not in wrapper
    assert 'Copy-Item -LiteralPath $SourceProbe' not in wrapper
