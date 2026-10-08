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


## Measured Windows decision — 2026-10-08: NO_GO

The user executed the initial PR #21 research harness on the independently
installed Runplane R130 wheel and supplied the full JSON proof:

```text
Runplane .dist-check/manifest-enumeration-01/measurements/manifest-enumeration-proof.json
```

Environment: Windows 10.0.26300, Python 3.11.9. 40 alternating pairs.
Result: `pass=true`, `candidate_eligible=true` *for the installed
67-manifest tree only*, and an unchanged before/after inventory
(67 plugins, 29 canonical effects, SHA256
`312409d73f8e1cd4a7b2e6d21f9d8864895bdadf08571d3d89389a8a5e403ff8`).

| Measurement | rglob | os.walk |
| --- | ---: | ---: |
| Median, milliseconds | 5.4611 | 5.4276 |
| Mean, milliseconds | 5.5537 | 5.4914 |
| p95, milliseconds | 6.0759 | 5.8860 |
| Samples | 40 | 40 |

The median difference is **0.0335 ms** (~0.61% of rglob enumeration
time). This difference is too small, relative to observed spread and
~50 ms total SDK bootstrap, to justify replacing production discovery.
It is **not** a measured SDK-level speedup.

The Windows case-parity test exposed a **path spelling difference**,
not a difference in discovered file membership. On Windows/Python 3.11,
`Path.rglob("manifest.json")` finds `MANIFEST.JSON`, but produces a
path spelling ending with `manifest.json` (the lowercase pattern).
The `os.walk` candidate emits the actual directory-entry spelling
`MANIFEST.JSON`. Both paths refer to the same underlying file on the
tested case-insensitive filesystem. Exact path-string sequence parity
still fails, and downstream consumers can observe the spelling difference.
This does **not** demonstrate that the baseline omits an uppercase file.
The original assertion that baseline discovery returned no files was
incorrect; the later test fixes that expectation.

The original focused gates:

- Ruff lint: PASS;
- Ruff format: two files required formatting (style only);
- Enumeration tests: **12 passed, 1 failed** (Windows case mismatch);
- Merged path-security baseline: **14 passed, 3 skipped** (file
  symlink creation denied by WinError 1314);
- Intra-discovery mutation guards: **3 passed**;
- Independent 40-pair enumeration pilot: **PASS**, with the narrow
  `candidate_eligible=true` reported above.

The branch repairs the formatter issues and makes the Windows
case test assert that **both enumerators discover one file**, but the
returned path strings differ in case. The test checks
`Path.samefile()` on the lowercased baseline spelling to separate file
identity from representation. It also improves `first_differences` to
include paths present on only one side of a sequence comparison
(the earlier `zip` stopped at the shorter list).

**Decision: NO_GO for replacing `Path.rglob` with this `os.walk`
candidate.** Keep the existing loader unchanged. Merging the research
script, parity tests, and documentation, if their final gate is green,
records negative evidence; it does not approve an implementation PR.

Further filesystem optimization would need a distinct high-ROI
hypothesis, full security equivalence and explicit owner justification.
No changes to `ManifestSource.discover`, `_read_manifest`, or
`_ensure_under_root` are proposed here.

### Final-gate correction after initial NO_GO documentation

The user's subsequent run on
`231b985ebfcd02956441a6275c9f5e19e1991de6` showed:

- Ruff formatter: **PASS** (2 files formatted);
- Ruff check: **one I001** (import order);
- Focused tests: **13 passed, 1 failed**, solely the Windows case test;
- Failure evidence: baseline included one
  `...\\pack\\manifest.json` path, not an empty list.

The newest changes reorder the one import and correct the test and
documentation to match the observed **file-found/path-spelling-differs**
behavior. These changes do not modify the performance harness's
enumeration algorithms or any production source. The new exact HEAD
still requires a short Ruff and focused-test gate.

The **NO_GO** decision remains unchanged: negligible median difference
of 0.0335 ms for the installed 67-manifest tree, and no proof of general
path-string behavior parity. Do not turn this into an `os.walk`
production optimization.
