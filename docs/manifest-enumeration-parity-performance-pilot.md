# Manifest enumeration parity and performance — research only

## Hypothesis

Installed-application profiling suggests that recursive discovery of manifest
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
for one private installed-application plugin tree. Parity on that tree is
necessary but is **not yet sufficient** for production eligibility.

## Local development gate

Run from the Invariant repository root in Python 3.11+:

```powershell
python -m pytest -q tests/core/test_manifest_enumeration_parity_pilot.py
python -m pytest -q tests/core/test_manifest_path_security_equivalence.py
python -m pytest -q tests/core/test_manifest_intra_discovery_mutation_guards.py
```

Optional format and lint checks, when Ruff is installed:

```powershell
$files = @(
  "examples/profile_manifest_enumeration.py"
  "tests/core/test_manifest_enumeration_parity_pilot.py"
)
python -m ruff check $files
python -m ruff format --check $files
```

## Scope of the measured consumer experiment

The historical 40-pair timing experiment used an independently installed,
**private** application with 67 manifests. The private application, its
installation instructions, inventory and proof artifacts are not published
here. The timing values below are retained as bounded historical research
evidence; they cannot be independently reproduced with this repository alone.

The experiment recorded alternating enumeration timings, exact ordered
paths, before/after inventory comparison and observed OS-specific casing
differences. It did **not** profile manifest parsing, path containment,
registration or application effects.

## Decision rule

Reject the replacement if ordered paths, duplicate behavior, case
matching or junction traversal disagree; if plugin inventory changes;
or if measured savings are too small to matter against total ~50 ms
SDK bootstrap. An unobserved symlink OS behavior is not positive proof.

There is no runtime optimization and no speedup claim in this PR.


## Measured Windows decision — 2026-10-08: NO_GO

A private installed-wheel application was used for the initial PR #21 research measurement. The original proof artifact is not distributed with Invariant.

Environment: Windows 10.0.26300, Python 3.11.9. 40 alternating pairs.
Result: `pass=true`, `candidate_eligible=true` *for the installed
67-manifest tree only*, and an unchanged before/after plugin inventory.
Neither the private inventory nor its checksum is included as a public fixture.

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

A subsequent local run on
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

## CI portability repair — 2026-10-14 (pending matrix verification)

GitHub Actions on `main` at commit `68ff60e04cdfbac3f43a8b80901b15fc74e6d8d2`
reported a red core matrix: Python 3.11 passed on Ubuntu and Windows,
while Python 3.12–3.14 failed only the research-only manifest enumeration
parity assertions. Logs: [CI run 38123727386](https://github.com/kymuco/invariant-runtime/actions/runs/38123727386).
In particular:

- The nested-folder test required `Path.rglob()` and `os.walk()` to
  return the same *order* on every Python release and filesystem. That
  is not a documented enumeration-order guarantee.
- The Windows mixed-case test hard-coded a specific Python 3.11
  path-spelling observation (lowercased `Path.rglob()` result), although
  the CI newer-Python baseline returned the actual uppercase spelling.
  Both discovered the same filesystem object on the tested Windows
  runner.

This small test-only repair retains the *exact ordered path-string*
eligibility rule and all existing negative parity checks. The
positive tests require full discovered-path membership with exact counts;
they compute eligibility from observed sequences rather than assert
a universal order or case rewrite. A new deterministic negative case
requires the comparison to reject **two spellings for the same file**
with distinct path strings. The producer `examples/profile_manifest_enumeration.py`
and production `ManifestSource.discover` remain unchanged.

The measured production optimization decision is still **NO_GO**.
A green research test means the comparison correctly reports the observed
differences; it is not evidence that the proposed `os.walk` implementation
is semantically equivalent on every Python/OS combination.

Scope of this change: research pilot tests + explanatory docs only.
The CI matrix must pass on the new branch before claiming CI recovery.
