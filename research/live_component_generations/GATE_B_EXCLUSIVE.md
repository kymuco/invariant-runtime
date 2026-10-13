# Gate B.2 — stop-first exclusivity with real Invariant plugin lifecycles

**Status: authored research experiment; NOT YET RUN on user Windows.**
Nothing was modified under invariant/ or main.

## Incoming evidence

- Gate B.1 (leased concurrent lightweight generations): 43 scoped tests
  reported green by user on Windows (2026-10-13).
- Gate A.5 readiness + regressions: 30 scoped PASS user Windows.
- Earlier standalone stop-first prototype: 15 test methods existed.
  No claim that an exact aggregate suite was rerun at this revision.

## Architectural contrast: lightweight vs exclusive

Lightweight replacement can prepare v2 beside v1, publish one READY route,
and drain v1 after its last lease (B.1). An exclusive provider that consumes
nearly all VRAM cannot necessarily start v2 beside v1. Its valid transition is:

1. Validate immutable candidate *metadata* and exact dependency graph only;
   do not allocate candidate hardware resources yet.
2. Fence new call admissions. Wait a bounded interval for existing borrowed
   operations to finish. If drain times out: restore old admission with
   v1 still running; no stop, no new start.
3. Stop v1's actual ExecutionContext and dependencies. Independently poll
   the HOST-provided resource-free predicate with a separate bounded deadline.
4. If not proven released: enter terminal FAILED fence. Never start v2.
5. If proven released: create/initialize the real v2 ExecutionContext,
   validate method, and only then publish its new active route.
6. If candidate startup fails, reconfirm resource release. Restore a FRESH
   v1 instance ONLY if that resource is proven free. Restoration does not
   preserve in-process model state or undo external effects. Otherwise
   enter FAILED and require explicit operator/host recovery.

This includes a deliberate **unavailable interval** while switching.
No claim of uninterrupted response service.

## Implementation

- stop_first.py: preserves all earlier test-double logic and adds
  terminal close(), is_closed, bounded lease drain, resource-release
  verification and fail-closed state on uncertain teardown.
- gate_b_exclusive_host.py: uses the existing StopFirstSlot as the SOLE
  published routing authority; maps each selected route to one retained
  CatalogView + real ExecutionContext + actual initialized plugin.
  Metadata stage contains an exact graph, signature, old route identity
  and unique stage ticket; no v2 instance is created at stage time.
  The complete host-level replace preflight occurs BEFORE drain begins.
  Host only supplies resource observation and chooses the named method;
  it does not become a second plugin registry.
- test_gate_b_exclusive_host.py: 12 new actual Invariant integration tests
  use file-based exclusive resource ownership. The actual TTS.initialize()
  writes a claim file and logs initialization; shutdown() removes it.
  A second model cannot initialize while the first claim exists.
  Tests include negative paths for lying shutdown, drain timeout,
  incomplete cleanup, failed candidate, stale stage and final close.
- test_stop_first.py: 3 additional stdlib-only tests of close semantics.

The file lock is a MOCK RESOURCE, not a reliable interprocess lock,
GPU driver query, VRAM ownership proof, or production resource scheduler.
All generated plugin code and test manifests are isolated in temporary
directories. Source hashing still covers only the manifest and primary
plugin.py, not transitive imports or model weights.

## New test coverage (15 authored)

12 real-object integration tests:

1. Stop v1 before starting v2; no double allocation; actual lifecycle
   event order and new routing are checked.
2. During a blocked old call, fence new admission, wait then switch;
   preserve old result, allow new calls after activation.
3. Drain timeout keeps original v1 operational.
4. Missing resource-free proof enters FAILED and never starts v2.
5. Candidate failure before allocation -> fresh v1 restoration.
6. Candidate failure after allocation with proper cleanup -> restoration.
7. Candidate failure with leaked exclusive resource -> FAILED, no restore.
8. Modified source between stage/replace rejected before stopping v1.
9. Stale stage cannot supersede a completed new route.
10. Terminal close stops provider, checks release and fences new calls.
11. Terminal close drain timeout preserves old ongoing borrow/admission.
12. Terminal close without release evidence stays FAILED, not clean.

Three standalone slot tests:

13. Terminal close is idempotent and prevents new install/replace.
14. Closing with an outstanding lease times out without stopping it.
15. Terminal close with a false stop / still-owned resource fails closed.

**Expected total scoped run:** 12 new real-object + 18 stop-first
(15 prior + 3 new) + 13 Gate B.1 + 8 Gate A.5 + 8 Gate A.4 +
4 Gate A.3 + 10 Gate A.1 = **73 tests**, provided all pass.
The separate old negative drift proof is not included.

## Windows gate

~~~powershell
Set-Location W:\dev\invariant-runtime
git fetch origin
git switch research/catalog-snapshot-gate-a-2026-10-11
git pull --ff-only origin research/catalog-snapshot-gate-a-2026-10-11

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

foreach ($file in @(
    "test_gate_b_exclusive_host.py",
    "test_stop_first.py",
    "test_gate_b_leased_host.py",
    "test_gate_a_readiness_host.py",
    "test_gate_a_catalog_owned_host.py",
    "test_gate_a_pinned_execution.py",
    "test_gate_a_catalog_view.py"
)) {
    & $python -m unittest discover -s research/live_component_generations -p $file -v
    if ($LASTEXITCODE -ne 0) { throw "GATE_B2_FAILED: $file" }
}

Write-Host "GATE_B2_EXCLUSIVE_SCOPED_OK"
~~~

Runtime deliberately logs expected initialization exceptions in failure
tests. Treat final test status/exit code, not intermediate stack traces,
as proof. If tests fail, do not weaken assertions to force green.

## Production NO-GO

- Exclusive release predicate is host-owned and must be independently
  trustworthy. Python context.close() alone is NOT physical VRAM proof.
- This experiment does not integrate with NVIDIA NVML, CUDA memory
  reconciliation, actual 12GB LLM weights, or process/worker health.
- No persistent singleton/mixed scope guarantees, no async generator
  leases, no leaked raw-reference safety, no external permission authority.
- Stop-first may create service downtime; candidate failure may leave
  FAILED with manual recovery, even when the host application stays up.
- A restart of the old plugin creates a fresh instance, not a resumption
  of previous state, and never replays completed/uncertain RPC effects.
- Fully immutable Python source/module/weight identities remain unsolved.
- The test method call is synchronous, with in-flight work tracked only
  through the StopFirstSlot lease. Background tasks outliving call return
  violate this research boundary.
- No production core promotion until explicit public owner contract,
  complete Invariant baseline tests, supported-Python matrix, Windows
  native release evidence and independent failure-injection gate.

If these 73 scoped tests pass, Gate B.2 can be called **research hypothesis
supported by user-Windows**. It is NOT a product-ready GPU swap.
