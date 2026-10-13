# Gate B.1 — leased retirement of real Invariant execution generations

**Status:** research candidate authored, NOT YET RUN on user Windows. No changes to production invariant/ or main.

## Previous evidence

- Gate A original catalog and service: 40 scoped PASS (user Windows).
- Mutable registry drift: negative characterization PASS (bug reproduced).
- Gate A.3: 4/4 PASS (user Windows).
- Gate A.4: 22/22 scoped PASS (user Windows).
- Gate A.5: 30/30 scoped PASS (user Windows; reported October 13, 2026).
- These are scoped user-run checks, not a full production suite.

## Scope

Gate A.5 retains all old execution contexts indefinitely to prevent use-after-shutdown and cannot safely race shutdown with in-flight calls.

Gate B.1 adds research-only LeasedReadinessHost, separate from existing Gate A.5 code. It uses a single published route pointer (catalog view + ready plugin), and accounting records for old generations. Counters and CAS publication share one lock.

- Admission increments the current generation's lease counter atomically with selection.
- A synchronous call releases its lease in a finally block, including plugin exceptions.
- On publication, old generation is marked retired. If its counter is zero, its real ExecutionContext closes outside the routing lock; otherwise the last lease release claims cleanup.
- Closing host rejects new calls, discards unpublished candidates and retires active generations. Active leases complete; close is nonblocking and does NOT guarantee all resources are already freed.
- A failed cleanup is recorded and retained as uncertain; do not silently retry or count the resource as released.
- Existing ReadinessGatedHost intentionally keeps its previous lifecycle behavior for regression coverage.

Only one root's mandatory EXECUTION plugin dependencies, synchronous method calls and explicit bounded borrow contexts are supported. No raw managed get, leaked reference tracking, background work, mixed policy scopes, external authority or GPU resource verification.

## 13 new test methods

1. Retire immediately when old generation has zero users.
2. In-flight call survives switch; old generation releases after its return.
3. Two overlapping borrows; release only after both finish.
4. Exception in borrower still drains old generation.
5. Host close fences new calls without interrupting an existing borrower.
6. Host close discards a pending candidate.
7. Stale candidate fails CAS without disrupting winner.
8. Candidate initialization failure preserves old active leased route.
9. Close while real method is blocked; eventual cleanup.
10. Repeated close never double-shuts down old generation.
11. Three epochs: old v1 borrowed, v2 retires independently, v3 active.
12. Failed shutdown stays quarantined, with explicit diagnostics.
13. Close during publication preflight rejects the prepared candidate.

Expected scoped run: 13 new + 8 Gate A.5 + 8 Gate A.4 + 4 Gate A.3 + 10 Gate A.1 = **43** tests. Original 40 and the negative regression are independent.

## Windows gate

~~~powershell
Set-Location W:\dev\invariant-runtime
git fetch origin
git switch research/catalog-snapshot-gate-a-2026-10-11
git pull --ff-only origin research/catalog-snapshot-gate-a-2026-10-11
$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
foreach ($file in @(
    "test_gate_b_leased_host.py",
    "test_gate_a_readiness_host.py",
    "test_gate_a_catalog_owned_host.py",
    "test_gate_a_pinned_execution.py",
    "test_gate_a_catalog_view.py"
)) {
    & $python -m unittest discover -s research/live_component_generations -p $file -v
    if ($LASTEXITCODE -ne 0) { throw "GATE_B_SCOPED_FAILED: $file" }
}
Write-Host "GATE_B_LEASED_REAL_RUNTIME_OK"
~~~

Intentional candidate startup failure traceback in prior Gate A tests is expected; check exit status and final OK.

## Explicit no-go

- Not a production PluginService API, general plugin lifetime manager, or safety boundary.
- Caller must not hold plugin object beyond acquire() scope. Asynchronous work, returned generators, background threads and plugin-initiated tasks are NOT covered.
- Software shutdown is not evidence of freeing GPU memory. A failing close is quarantined; operator/host must reconcile resources.
- Candidate preparation overlaps old generations. Resource-exclusive LLMs require a host-governed stop-first, drain, release-check and explicit unavailable interval.
- Plugin artifacts are not immutable (only manifest and one Python file fingerprints); transitive imports and weights are untracked.
- No bounded waiting of user calls, no worker crash supervision, no full Python/Windows suite, and no production promotion authorization.

Next research target, only after this scoped Gate B.1 is green: exclusive stop-first with bounded drain and host-provided real resource release evidence.
