# Gate A.5 — readiness-gated catalog + route publication

**Status:** research-only candidate authored; new integration tests have NOT
yet been run against the user's exact Invariant checkout. No modifications to
production `invariant/`, to `main`, or to public `PluginService` APIs.

## Evidence entering Gate A.5

- Original snapshot integration: 40 scoped tests PASS (user Windows).
- Negative production activation-drift characterization: PASS
  (meaning the drift was reproduced on user Windows).
- Gate A.3 pinned positive activation: 4/4 PASS (user Windows).
- Gate A.4 single authoritative catalog experiment and regressions:
  22/22 PASS (user Windows, reported 2026-10-13).
- None of these is a full suite, multi-scope or GPU claim.

## The remaining failure

Gate A.4 `CatalogOwnedHost.publish(stage)` updates the published
`CatalogView` before attempting to construct the root plugin instance.
A new execution can therefore encounter a broken plugin (e.g. a failing
`initialize`) after the epoch became globally visible.

## Pilot A.5: one atomic *published route*

`gate_a_readiness_host.py` creates `ReadinessGatedHost`, an opt-in,
research-only synchronous method router for **one fixed root plugin id**.
Each `PublishedRoute` owns a `CatalogView`, a real
`ExecutionContext`, the initialized root instance and its exact identity.
The host maintains **one** authoritative active-route pointer; it does
not publish a separate catalog pointer or mutable PluginRegistry.

Sequence:

1. `stage()`: discover and validate exact versioned metadata; reject
   removed/edited versions, incomplete graphs, unsupported scopes and
   stage with no newer routable root version.
2. `prepare(stage)`: create a real context pinned to the candidate view;
   initialize actual plugin/dependencies with Invariant's ordinary
   `ExecutionRuntime` and `ActivationTransaction`; verify the named method
   is callable. **No publication** occurs.
3. `publish_ready(prepared)`: freshly check manifest+primary source
   fingerprints, stale base-route CAS and prepared status. Under one
   lock, assign the single active route that contains both catalog epoch
   and the already-ready instance. The prepared instance is **reused**,
   not initialized a second time.
4. On prepare or publish failure, close the candidate context and keep the
   previous route unchanged. Explicit `discard(prepared)` aborts an
   unpublished candidate. `close()` cleans up all retained instances,
   provided that the outside caller first quiesces operations.

A method invocation captures one active route, releases the lock, then
calls its prepared instance. The old route is retained after switching,
so calls that were admitted before publication can complete without
use-after-shutdown. **Old contexts are not automatically retired or
released**; they are kept until an external quiesced `close()`.
This is NOT a memory-efficient solution for overlapping 12-GB VRAM LLMs.

## Eight authored real-object checks

`test_gate_a_readiness_host.py`:

1. Broken candidate initialization does not publish or stop old routing.
2. Prepared instance identity is preserved through publication; old
   and new instances shut down only on quiesced close.
3. Candidate missing its promised method cannot publish.
4. Source edit after preparation aborts publish and closes candidate.
5. Stale competing prepared candidate loses CAS and is closed.
6. Blocked candidate preparation leaves old routing responsive.
7. In-flight old call completes after a new route publishes.
8. Discard and closed-host operations fail closed.

Intentional startup failure traces in tests 1/3 are expected; final
`OK` is the only reliable all-green signal.

## Exact Windows check

```powershell
Set-Location W:\dev\invariant-runtime
git pull --ff-only origin research/catalog-snapshot-gate-a-2026-10-11

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

foreach ($file in @(
    "test_gate_a_readiness_host.py",
    "test_gate_a_catalog_owned_host.py",
    "test_gate_a_pinned_execution.py",
    "test_gate_a_catalog_view.py"
)) {
    & $python -m unittest discover -s research/live_component_generations -p $file -v
    if ($LASTEXITCODE -ne 0) { throw "GATE_A_REGRESSION_FAILED: $file" }
}
Write-Host "GATE_A_READINESS_SCOPED_OK"
```

Expected 8 + 8 + 4 + 10 = **30** scoped methods, if all pass.
Re-run negative characterization independently to keep the diagnosis
separate from the positive prototype.

## Explicit NO-GO for production

- The new host is deliberately not a `PluginService` replacement.
  It only routes one synchronous root method for mandatory EXECUTION
  dependencies. No SINGLETON, TRANSIENT, PLUGIN or optional plugin
  dependency guarantees.
- Readiness is only observed at preparation time. It does not prove
  the component can never crash later, nor that user-specific calls
  always succeed.
- `call` and `close` are NOT safe to race against each other. This
  research host requires the caller to quiesce all invocations first.
  Without leases, auto-reclaim is unsafe (Gate B).
- Candidates are prepared **alongside** old versions. In an exclusive
  resource situation (e.g. 12-GB GPU memory used by an LLM), preparation
  may OOM or fail before publication. Exclusive `stop-first` is a
  different host-controlled transaction; this pilot does not pretend
  it works for that case.
- Manifest and one primary Python file are fingerprinted, not complete
  transitive imports, weights, editable symlink trees or loader cache
  identity. There are filesystem TOCTOU windows.
- There is no automatic rollback once a successfully prepared version
  is published and later fails during an actual call.
- Policy/permission boundaries remain host-owned, not inferred from
  catalog readiness.
- Nothing in this research pilot authorizes a production runtime
  change without an owner contract and a full baseline suite.
