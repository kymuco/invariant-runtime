# Gate A.3 — pinned activation against real Invariant objects

**Status:** research-only candidate, not production code, not public API.

## Previous evidence

- Original 40 Gate A tests: user-Windows PASS at commit
  `5139fc7640cd65cabb64cd8b93623d6607a35152`.
- Negative activation characterization: user-Windows PASS at commit
  `25bb506a69d7743f560bf99980a927481ccf3dfe`. That PASS means the
  original mutable-runtime graph/actual dependency mismatch was reproduced.
- Four new positive tests: **AUTHORED, NOT YET RUN** against the private
  Invariant checkout. No assistant-local integration, CI, full-suite, or
  GPU proof is claimed for these tests.

## Exact gap

The current production `ExecutionRuntime.get` collects a graph from a
mutable registry, then `ActivationTransaction.dependency_get` and
`ExecutionRuntime._create_and_init` resolve raw dependency refs again.
If `research:lex` is unpinned, registering v2 between graph collection
and instance initialization can inject v2 although the graph specified v1.

## Research-only alternative

`gate_a_pinned_execution.py` provides two opt-in helpers.

- `capture_catalog_view(service)` copies live descriptors under the
  registry's list lock; uses a separate registry to choose exact versions
  for mandatory EXECUTION dependencies; builds one validated CatalogView.
- `open_pinned_execution(service, view)` calls the real private
  `ExecutionContext` with a narrow research-only service bridge. Its
  `registry` points to the immutable view; its loader and tracer remain
  the original service's. The genuine policy gates, resolver,
  ActivationTransaction and plugin lifecycle continue to run.

No production runtime file is changed. Other callers of
`PluginService.get()` and ordinary `service.execution()` remain
unmodified. This is **not an in-place PluginService refresh contract**.

Supported scope: EphemeralPolicy and mandatory EXECUTION plugin edges.
SINGLETON, TRANSIENT, PLUGIN and optional plugin dependencies fail closed
when opened through this bridge. Capability refs still pass through the
real runtime's policy-gated capability lookup, but this new test suite
does not separately qualify capability policy behavior.

## Authored real-object tests

`test_gate_a_pinned_execution.py` contains four tests:

1. After graph collection, add lex v2 to the live registry at the exact
   previous failure point. The old pinned context must **inject lex v1**
   (whereas the original negative test injected v2). New captures may
   explicitly select v2.
2. Stage/publish TTS+lex v2 with CatalogViews CAS while retaining a v1
   context. Old context returns v1; new context returns v2; the old
   PluginService registry is deliberately not updated by view publication.
3. Candidate TTS v2 initialize fails. Previously active v1 context
   continues operating. This does not prove automatic binding rollback.
4. Mixed SINGLETON dependency is rejected during capture rather than
   silently receiving unsafe lifetime guarantees.

For unpinned manifest refs, the recorded closure of an unchanged version
cannot be silently rebound by CatalogViews.stage: the research epoch
planner deliberately treats this as an immutable-identity conflict.
A new separate capture may resolve latest again, but this is not
an automatically publishable update transaction.

## Windows integration gate

Run from the same branch as the original green negative test:

```powershell
Set-Location W:\dev\invariant-runtime
git fetch origin
git switch research/catalog-snapshot-gate-a-2026-10-11
git pull --ff-only origin research/catalog-snapshot-gate-a-2026-10-11
$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
& $python -m unittest discover -s research/live_component_generations -p "test_gate_a_pinned_execution.py" -v
if ($LASTEXITCODE -ne 0) { throw "GATE_A_PINNED_ACTIVATION_FAILED" }
Write-Host "GATE_A_PINNED_ACTIVATION_OK"
```

These tests and the prior negative test should retain opposite expected
results; do not edit the negative one to make the original runtime look fixed.

## Gate status

**Gate A metadata snapshot:** user-Windows PASS.

**Gate A original production activation bug:** user-Windows reproduced.

**Gate A pinned activation positive proof:** pending user-Windows tests.

**Production promotion:** NO-GO. Real descriptor ownership must become
single-source, activation must pin one graph across all scopes and
inject only exact refs without weakening capability policy, and code
artifact identity must be pinned. No tracked leases, singleton safety,
GPU/resource release, worker recovery or true hot-swap is demonstrated.
