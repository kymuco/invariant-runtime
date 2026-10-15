# Independent installed-wheel consumer proof — Runplane × Invariant

**Status: scoped native Windows installed-wheel consumer E2E PASS (operator output, 2026-10-15). Not production-approved and not a dependency-pin update.**

## Purpose

Check the behavior of a REAL separately distributed Runplane application
using the candidate Invariant Runtime from the lifecycle fix stack
(PR #24 → #25 → #26). Avoid source checkouts in the Python import
environment and do not quietly resolve Runplane's OLD Invariant pin.

## Exact compatibility matrix

| Input | Locked commit |
|---|---|
| Invariant candidate | `0f81d09f517390fc2f0ae0a6ccce55eb56316763` (PR #26 head; includes #24 and #25) |
| Runplane private repository | `8385b348b54901103feab887f453a0ae36daa71f` (main snapshot) |
| Runplane-declared historic Invariant dependency | `3ef4166c3f9e24a6050c1076ad9af846f4f9af63` |
| Python | Activated Windows Python >=3.11; fresh venv created by harness |

**Compatibility override is explicit**: Runplane's `pyproject.toml`
requires Invariant via a fixed older Git SHA. This gate builds both
distribution wheels and installs with `pip install --no-deps`.
That proves only the chosen prospective candidate pairing and does NOT
make Runplane's pinned, default `pip install .` installation use this
Invariant revision. The pinned consumer metadata is unchanged.

Both repositories are **private**; the Invariant repository's normal
GitHub Actions token does not implicitly have checkout rights for
Runplane. Therefore ordinary Invariant CI only verifies its own tests;
a cross-repository local Windows consumer gate is required unless
explicit authorized cross-repository CI credentials become available.
Do not call an ordinary core CI run a successful external consumer E2E.

## Exact proof

Source files (in this research branch, **not included in Invariant wheel**):

- `tools/runplane_installed_wheel_proof.ps1` — Windows CLI orchestration,
  pinned refs, isolated venv, wheel builds and imports, cleanup.
- `tools/runplane_installed_compat_probe.py` — actual installed consumer.

The PowerShell wrapper:
1. Fetches only refs into each existing authenticated **local** Git repository.
2. Creates detached temporary **worktrees** at the exact pinned commits,
   outside both checkouts; never checks out or edits original worktrees.
3. Builds two distribution wheels using `pip wheel --no-deps` into a
   unique temporary proof root. Uses a fresh venv.
4. Installs both candidate wheels with `pip install --no-deps`.
   It must NOT `pip install runplane` with dependency resolution, which
   would fetch the old Invariant pin.
5. **Separately fetches and pins the research branch Git object** for
   `tools/runplane_installed_compat_probe.py`, because that probe does
   *not* exist in the older pinned candidate Invariant wheel commit.
   Extracts that verified object into the external proof directory, then
   launches it under `venv/python -I`. Both module `__file__` paths
   must come from that environment's `site-packages`, not either checkout.
6. Tests real `Runplane.create()`, the installed required plugin inventory,
   `invariant:echo` initialized plugin, all **29** canonical effect
   descriptors, actual `workspace.path_stat` and `workspace.read_file`,
   and exact byte-for-byte unchanged caller-owned fixture.
7. Separately constructs Invariant's internal diagnostic `RuntimeHost`
   with a REAL Runplane `RunplaneService` and a real `invariant:echo`
   plugin. Tests create execution → plugin_get → ping →
   host.close → no remaining execution → no new admission →
   idempotent host.close. This probe intentionally imports
   `invariant.ops.host` **inside the test**, NOT in Runplane production.
8. Removes temporary worktrees on completion/failure while preserving
   the proof root wheelhouse, fresh venv, and copied script as evidence.

Successful output must include BOTH:
- `INVARIANT_RUNPLANE_WHEEL_PROOF_JSON={"pass":true,...}`
- `INVARIANT_RUNPLANE_INSTALLED_WHEEL_GATE_OK`

No pass is claimed until the Windows invocation exits 0 and these
markers plus the actual installed import locations are seen.

## Windows invocation

From any PowerShell working directory (does not require switching Git
branches), fetch the proof branch and extract the orchestrator with
`git show`, or use a temporary separate checkout of that branch:

```powershell
$invariant = "W:\dev\invariant-runtime"
$runplane = "W:\dev\hde\runplane"

git -C $invariant fetch origin research/runplane-installed-wheel-consumer-2026-10-15

# Export the script ONLY to an external file; no git switch of your checkout.
$script = Join-Path $env:TEMP "invariant-runplane-consumer-proof.ps1"
git -C $invariant show "FETCH_HEAD:tools/runplane_installed_wheel_proof.ps1" |
    Set-Content -LiteralPath $script -Encoding UTF8

& $script -InvariantRepo $invariant -RunplaneRepo $runplane
if (-not $?) { throw "INVARIANT_RUNPLANE_PROOF_FAILED" }
```

**Alternative:** if this research branch is checked out, directly run
`& .\tools\runplane_installed_wheel_proof.ps1 -RunplaneRepo W:\dev\hde\runplane`.

## Recover the 2026-10-15 probe extraction failure without rebuilding

The first Windows report shows **both wheels built and installed**, then
`Copy-Item` failed: the script wrongly tried to read the research-only
Python probe from the older Invariant worktree at `0f81d09`. The
underlying installed consumer E2E **has not run**; the already-built
wheels do not need to be rebuilt merely to correct that script path.

The wrapper now fetches the research branch independently, pins its Git
object SHA and extracts the probe from that object, not from the runtime
worktree. If the original unique temp proof root still exists, it can be
reused for the actual Python E2E:

```powershell
$invariant = "W:\dev\invariant-runtime"
$root = "C:\Users\toumi\AppData\Local\Temp\invariant-runplane-wheel-b0c494c91b394c0ba7a4edb7b888820d"
$python = Join-Path $root "consumer-venv\Scripts\python.exe"
$probe = Join-Path $root "external_probe.py"

if (-not (Test-Path $python)) { throw "PRIOR_WHEEL_VENV_NOT_FOUND" }

git -C $invariant fetch origin research/runplane-installed-wheel-consumer-2026-10-15
if ($LASTEXITCODE -ne 0) { throw "PROBE_FETCH_FAILED" }
$probeSource = & git -C $invariant show "FETCH_HEAD:tools/runplane_installed_compat_probe.py"
if ($LASTEXITCODE -ne 0 -or -not $probeSource) { throw "PROBE_EXTRACTION_FAILED" }
$probeSource | Set-Content -LiteralPath $probe -Encoding UTF8

Push-Location $root
try {
    & $python -I $probe
    if ($LASTEXITCODE -ne 0) { throw "RUNPLANE_CONSUMER_E2E_FAILED" }
    Write-Host "INVARIANT_RUNPLANE_INSTALLED_WHEEL_GATE_OK"
}
finally {
    Pop-Location
}
```

This is the fastest recovery path for the *already built* candidate
wheels. Alternatively, fetch/export the updated wrapper with the original
invocation to rebuild from scratch. Do not label E2E PASS until the
probe itself exits zero with its evidence markers.


If the script fails, report the FIRST error and proof root path. No
manual edits to either source repository should be needed. Keep
`$ErrorActionPreference='Stop'`; do not treat stale `$LASTEXITCODE`
after a PowerShell command-not-found as test success.

## Acceptance and non-goals

PASS establishes compatibility of these exact two independent wheel
builds for real read-only filesystem effects, manifest loading, plugin
activation, and single execution host shutdown. FAIL blocks changing
Runplane's real pinned dependency until corrected.

The script does NOT exercise the blocked concurrent shutdown scenario
on a real plugin; deterministic concurrent lifecycle regressions are
covered in the underlying Invariant suite. It does NOT prove:
GPU loading, desktop automation reliability, network/process effects,
hostile-plugin containment, atomic package migration, or general
compatibility with future Runplane commits. It does not change any
Runplane code or release pins.

**No automatic merge and no Runplane version-pin update** from a single
E2E proof. A pin update should have a separate Runplane-owned
compatibility/release decision and full consumer regression evidence.

## Verified Windows installed consumer E2E — 2026-10-15

The operator successfully resumed the prebuilt independent wheel environment
without rebuilding either package. Windows PowerShell fetched the research
probe with `git show FETCH_HEAD:tools/runplane_installed_compat_probe.py`,
copied it to the proof root and executed it with `consumer-venv\Scripts\python.exe -I`.
The actual terminal output confirms the following exact evidence:

```text
INVARIANT_RUNPLANE_WHEEL_PROOF_JSON={"canonical_effects": 29, "consumer_scope": "real_installed_runplane_sdk_readonly_plus_real_plugin_host_close", "historical_pin_bypassed_for_candidate": true, "invariant_import": "C:\\Users\\toumi\\AppData\\Local\\Temp\\invariant-runplane-wheel-b0c494c91b394c0ba7a4edb7b888820d\\consumer-venv\\Lib\\site-packages\\invariant\\__init__.py", "invariant_version": "0.1.0", "pass": true, "plugins_discovered": 67, "runplane_import": "C:\\Users\\toumi\\AppData\\Local\\Temp\\invariant-runplane-wheel-b0c494c91b394c0ba7a4edb7b888820d\\consumer-venv\\Lib\\site-packages\\runplane\\__init__.py", "runplane_version": "0.1.0a0", "schema": "INVARIANT_RUNPLANE_INSTALLED_WHEEL_CONSUMER_V1"}
INVARIANT_RUNPLANE_INSTALLED_CONSUMER_PASS
INVARIANT_RUNPLANE_INSTALLED_WHEEL_GATE_OK
```

**Verdict: SCOPED PASS for the exact candidate pair**:
Invariant `0f81d09f517390fc2f0ae0a6ccce55eb56316763`, Runplane
`8385b348b54901103feab887f453a0ae36daa71f`. The proof checks
real installed public Runplane bootstrap, 67 discovered plugins, 29
canonical effects, initialized `invariant:echo`, read-only
`workspace.path_stat` / `workspace.read_file` on a disposable file
with unchanged SHA-256, plus an internal diagnostic
`RuntimeHost` + actual `RunplaneService` create/get/ping/close path.
Import locations are both within isolated wheel environment
`site-packages`. Source trees and main branches remained unchanged.

The first attempt failed at the research probe's incorrectly chosen source
path (harness error), then this resumed attempt completed successfully
without rerunning the wheel builds. This supports the scoped candidate
consumer contract only. It does **not** prove the general concurrent
shutdown behavior inside Runplane's own SDK, production readiness, or
that default `pip install runplane` selects these wheel bytes: Runplane
still pins the older Invariant Git revision. That requires a separate
Runplane-owned dependency upgrade and regression checks if desired.
