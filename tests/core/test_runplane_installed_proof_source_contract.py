"""Keep the private-repo external consumer proof runnable independently.

The Invariant candidate wheel is intentionally pinned to a lifecycle
commit from BEFORE the proof-only Python script existed. This test
prevents accidentally reintroducing the impossible worktree copy.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


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
    assert '$ProbeObject = $ProbeCommit + ":tools/runplane_installed_compat_probe.py"' in wrapper
    assert "$ProbeCommit -notmatch '^[0-9a-f]{40}$'" in wrapper
    assert 'git -C $InvariantRepo show $ProbeObject' in wrapper
    assert '$ProbeSource | Set-Content -LiteralPath $Probe -Encoding UTF8' in wrapper
    assert r'Join-Path $InvariantTree "tools\runplane_installed_compat_probe.py"' not in wrapper
    assert 'Copy-Item -LiteralPath $SourceProbe' not in wrapper

@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell parser")
def test_wrapper_has_valid_windows_powershell_syntax():
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    script = ROOT / "tools" / "runplane_installed_wheel_proof.ps1"
    literal = str(script).replace("'", "''")
    parser = (
        "$tokens=$null; $errors=$null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{literal}', "
        "[ref]$tokens, [ref]$errors) | Out-Null; "
        "if ($errors.Count -gt 0) { $errors | Out-String | Write-Error; exit 1 }"
    )
    out = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", parser],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert out.returncode == 0, out.stdout + "\n" + out.stderr
