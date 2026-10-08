# Manifest enumeration parity and performance — research only

## Hypothesis

Installed Runplane profiling suggests that recursive discovery of manifest
files costs time, but earlier cProfile cumulative timings are perturbed by
instrumentation and cannot predict an unprofiled improvement.

This experiment compares the current `Path.rglob("manifest.json")` loop
with a read-only `os.walk(..., followlinks=False)` candidate **without
changing** the production `ManifestSource.discover` implementation.

## Success criteria and known limitations

Exact ordered path sequence (not merely set equality) must match across
all configured roots, including duplicate roots. The proof also records
a multiset comparison and the first differences when the sequences diverge.

The focused tests cover nested files, missing/duplicate roots, edits
between independent enumerations, order drift, Windows manifest filename
casing and NTFS junction traversal. The junction test records whether
the candidates match: a green research run is **not** sufficient to
declare parity if junction results differ.

The existing merged containment and within-discovery mutation tests
remain mandatory. They are not replaced by this pilot. The three file
symlink skips due to Windows WinError 1314 remain a coverage gap.

`pass=true` in the output means the experiment completed successfully;
`candidate_eligible=true` means exact sequence equality was observed
for the installed Runplane plugin roots. Parity on those roots is
necessary but is **not yet sufficient** for production eligibility.

## Local development gate

In the `W:\dev\invariant-runtime` checkout, after switching to the
research branch:

```powershell
$python = "W:\dev\hde\runplane\.venv-r2\Scripts\python.exe"
$files = @(
  "examples/profile_manifest_enumeration.py"
  "tests/core/test_manifest_enumeration_parity_pilot.py"
)
& $python -m ruff check $files
& $python -m ruff format --check $files
& $python -m pytest tests/core/test_manifest_enumeration_parity_pilot.py -q -rs
& $python -m pytest tests/core/test_manifest_path_security_equivalence.py -q -rs
& $python -m pytest tests/core/test_manifest_intra_discovery_mutation_guards.py -q
```

## Independent installed-wheel proof

The benchmark must be copied **outside** the invariant-runtime and
Runplane source checkouts and run with the existing installed R130
consumer Python. It uses installed Runplane package metadata to discover
the real plugin-pack roots and verifies SDK inventory before and after.

```powershell
$repo = (Get-Location).Path
$runplane = "W:\dev\hde\runplane"
$consumerPython = Join-Path $runplane ".dist-check\bound-effect-client-v1-02\consumer-venv\Scripts\python.exe"
$root = Join-Path $runplane ".dist-check\manifest-enumeration-01"
if (Test-Path $root) {
  throw "Use a new proof root; preserve earlier measurements"
}

$consumer = Join-Path $root "consumer"
New-Item -ItemType Directory -Path $consumer -Force | Out-Null
Copy-Item (Join-Path $repo "examples\profile_manifest_enumeration.py") $consumer

Push-Location $consumer
try {
  & $consumerPython (Join-Path $consumer "profile_manifest_enumeration.py")  `
    --proof-root (Join-Path $root "measurements")  `
    --samples 40
}
finally {
  Pop-Location
}
```

Result: `INVARIANT_MANIFEST_ENUMERATION_PILOT_JSON=...` and
`manifest-enumeration-proof.json` inside the new measurements directory.

The proof includes 40 alternated pairs, raw timing arrays, mean/median/p95,
exact manifest paths and a before/after plugin/effect inventory SHA256.
It does **not** profile manifest parsing, path containment, registration
or effect execution.

## Decision rule

Reject the replacement if ordered paths, duplicate behavior, case
matching or junction traversal disagree; if plugin inventory changes;
or if measured savings are too small to matter against total ~50 ms
SDK bootstrap. An unobserved symlink OS behavior is not positive proof.

There is no runtime optimization and no speedup claim in this PR.
