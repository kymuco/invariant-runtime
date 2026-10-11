# Gate A.4 — one published catalog owner (research only)

**Status:** candidate authored, **not yet run** on the new branch head.
No changes to `invariant/`, `tests/core/`, public API or `main`.

## Proven before this experiment

- Original Gate A snapshot checks: 40 scoped methods PASS (user Windows).
- Negative mutable-registry graph/injection drift: reproduced, PASS (user Windows).
- Gate A.3 pinned real-ExecutionContext integration: 4/4 PASS in 0.102 s
  (user Windows, `86a367f88a649f9be54b3e5130057063b6a70e67`).
  The intentional `candidate startup failed` traceback is expected.
- These are distinct proof boundaries, not a full-suite certification.

## Ownership experiment

`gate_a_catalog_owned_host.py` builds a host whose **only published
catalog** is `CatalogViews.snapshot()`. It uses real Invariant
`PluginLoader`, `ManifestSource`, `PluginRegistry` (temporary staging
validation), `DependencyResolver` and `ExecutionContext` objects.

Construction/discovery use detached registries only as local validation
scratch state. There is **no parallel mutable `PluginService.registry`
serving callers**. Publication is the `CatalogViews` single pointer move;
new `CatalogOwnedHost.execution()` obtains a retained view and passes it
to the existing research `open_pinned_execution()` bridge.

Thus the experiment does **not** claim that production `PluginService`
has gained hot refresh, nor that the research host is a drop-in
`PluginService` replacement. It is a bounded architectural candidate for
a future production owner/read path.

Normal `RuntimeInspector.list_descriptors()` requires `registry.list()`;
`CatalogView.list()` was added as a read-only facade over the retained
view, and the new tests verify that an existing context's inspector
still lists the old epoch after publication.

## Eight positive/negative integration assertions

`test_gate_a_catalog_owned_host.py` now defines **8** tests:

1. Old and new real plugin execution contexts each use the correct
   version after a catalog CAS update; the host has no independent
   public mutable `registry`.
2. RuntimeInspector inventory stays pinned to the captured view.
3. Concurrent staged contenders use CAS: the stale stage fails.
4. Source mutation after staging aborts publication.
5. Changing an unpinned dependency closure under the same root version
   is rejected (artifact identity ≠ mutable dependency binding).
6. Unsupported `SINGLETON` dependency fails closed at discovery.
7. Candidate activation failure leaves existing old context operational,
   but does **not** roll back the now-published catalog or make new calls
   work; activation readiness gating is not solved here.
8. Two manifest files with the same identity are rejected rather than
   being silently coalesced by the real loader.

All require actual Invariant Python runtime integration; authored
source is **not** evidence of these passing.

## Windows check

```powershell
Set-Location W:\dev\invariant-runtime
git pull --ff-only origin research/catalog-snapshot-gate-a-2026-10-11

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

& $python -m unittest discover -s research/live_component_generations -p "test_gate_a_catalog_owned_host.py" -v
if ($LASTEXITCODE -ne 0) { throw "GATE_A_SINGLE_OWNER_FAILED" }

& $python -m unittest discover -s research/live_component_generations -p "test_gate_a_pinned_execution.py" -v
if ($LASTEXITCODE -ne 0) { throw "GATE_A_PINNED_REGRESSION_FAILED" }

& $python -m unittest discover -s research/live_component_generations -p "test_gate_a_catalog_view.py" -v
if ($LASTEXITCODE -ne 0) { throw "GATE_A_VIEW_REGRESSION_FAILED" }
Write-Host "GATE_A_SINGLE_OWNER_SCOPED_OK"
```

**Expected:** 8 + 4 + 10 = 22 scoped test methods, if all pass.
Pre-existing deliberate plugin-initialize failure log is expected.

## Boundaries / risks

- Pure Python descriptors are not inherently immutable; mutable source
  paths, imported files and model weight files are not content-addressed.
- Fresh discovery and compare/signature checks close some races but do not
  lock the filesystem against mutation after publish.
- One published catalog is not automatically one authoritative instance
  cache, lifecycle manager or lease owner. Managed `get()` is intentionally
  **not** provided by this research host.
- Same-version dependency rebind after a `latest` upgrade is deliberately
  refused rather than transparently switching the old plugin's graph.
- Mixed policy scopes, external authority, environment isolation,
  unsupported inspector methods, arbitrary retained raw references,
  GPU/resource reclamation and real worker hot reload remain unproven.
- The old context can survive a broken new activation because it is still
  retained; this is **not** a readiness-gated routing transaction or
  automatic rollback.
- There is no automatic run/verification by the assistant against the
  private repository; provenance must remain user-Windows if executed there.

## Decision

**Gate A.4 remains OPEN pending Windows execution.** Even a green scoped
result is only permission to evaluate a narrow production-facing catalog
owner contract, not permission to copy research modules to `invariant/`.
Before a production change, separately prove publisher atomicity,
concurrent activation/instance ownership, model artifact identity,
existing `PluginService` compatibility and the full supported-Python
runtime suite.
