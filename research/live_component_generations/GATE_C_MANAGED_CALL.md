# Gate C.1 — opt-in bounded call port over existing Gate B owners

**Status: research PASS on user Windows (2026-10-13); not production approved.**
No code or public export changed under invariant/, and main remains untouched.

## Input evidence

- Gate A.5: 30 scoped tests PASS on user Windows.
- Gate B.1: 43 scoped tests PASS on user Windows.
- Gate B.2: 73 scoped tests PASS on user Windows.
- Gate B.3: 13 CPU evidence checks PASS and native CUDA smoke PASS on user's RTX 5070.
- Gate B.4: real manifest Invariant plugin controlling real CUDA subprocess PASS, exit=0. Catalog epoch 2, calls 1.0.0:first and 2.0.0:second, PIDs 25756 and 25292, CUDA free memory 6714 MiB before and 6717 MiB after.

## The change

`gate_c_managed_call.py` provides exactly one research opt-in path:

```python
with ManagedCallPort.exclusive(
    [plugin_root], root_ref='research:tts',
    is_resource_free=host_owned_probe,
) as port:
    answer = port.invoke('hello')
    next_catalog_epoch = port.upgrade()
    answer2 = port.invoke('hello again')
```

- The port does **not** create a registry, active binding, cache, second worker supervisor, epoch record, policy or scheduler.
- **Overlap mode** delegates each invocation and READY-publication to existing LeasedReadinessHost. Calls borrow exactly one generation until the synchronous method returns; retired generations drain and then close.
- **Exclusive mode** delegates to existing RealStopFirstHost and StopFirstSlot. A metadata-only stage precedes bounded drain, worker stop, independent release check, startup and publication. New admissions are explicitly unavailable during switching.
- Only one configured root and one configured method, accepting a plain str argument. The ONLY supported returned values are exact str or bytes. Mutables, iterators, generators, futures, plugin instances and deferred work are refused to prevent unsupported borrowed-object lifetime escape. Audio chunks/streams require a separately defined lease or streaming owner.
- The port cannot grant permission or trust by virtue of catalog presence. The trusted host must select the policy and authorize calls outside this research pilot.
- No public `.get()`, `.execution()` or `.acquire()` method is exposed by the port. Python private attributes are NOT a security boundary.
- Legacy `PluginService.get()` remains **unchanged** and keeps its raw managed object return contract. It is not claimed safe for hot-swapping under arbitrary retained references.
- Gate B owners retain the sole catalog/route authority. An operation returns the epoch for management observations, never a plugin instance.

## Real-object scoped verification

`test_gate_c_managed_call.py` contains **10** new CPU-only methods:

1. Overlap v1 to v2 call and idle old shutdown (including dependency).
2. Concurrent in-flight old call completes safely after new publication.
3. Deny raw plugin-object results, non-plain inputs, and raw-get surfaces.
4. Permit immutable bytes output for synchronous audio payloads.
5. Closed port fences invocation and upgrade.
6. Exclusive swap with real ExecutionContext and mock exclusive resource.
7. Drain-timeout keeps the original exclusive plugin callable.
8. Resource not released => FAILED, no v2 operation admitted.
9. During blocked old call, exclusive drain denies new calls and transitions cleanly.
10. Existing public PluginService.from_manifest_paths/get returns raw managed singleton unchanged.

### Windows CPU gate and regression

```powershell
Set-Location W:\dev\invariant-runtime
git fetch origin
git switch research/catalog-snapshot-gate-a-2026-10-11
git pull --ff-only origin research/catalog-snapshot-gate-a-2026-10-11

$python = '.\.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

foreach ($file in @(
    'test_gate_c_managed_call.py',
    'test_gate_b_cuda_evidence.py',
    'test_gate_b_exclusive_host.py',
    'test_stop_first.py',
    'test_gate_b_leased_host.py',
    'test_gate_a_readiness_host.py',
    'test_gate_a_catalog_owned_host.py',
    'test_gate_a_pinned_execution.py',
    'test_gate_a_catalog_view.py'
)) {
    & $python -m unittest discover -s research/live_component_generations -p $file -v
    if ($LASTEXITCODE -ne 0) { throw "GATE_C1_SCOPED_FAILED: $file" }
}
Write-Host 'GATE_C1_SCOPED_OK'
```

Expected **96** scoped methods if all pass: 10 new C.1, 13 B.3 CPU, 73 earlier B.2 and Gate A/B.1 regressions. This is not a full production test suite.

### Separate opt-in native GPU E2E

`gate_c_managed_gpu_e2e.py` reuses the earlier proven Gate B.4 plugin manifest builder and CUDA worker implementation. It routes both plugin calls and upgrade through **ManagedCallPort**, not directly through the old RealStopFirstHost. No new CUDA allocator or host scheduler was authored.

```powershell
& $python research/live_component_generations/gate_c_managed_gpu_e2e.py --device 0 --mib 32
Write-Host "GATE_C1_GPU_EXIT=$LASTEXITCODE"
```

Expected success marker: `GATE_C1_MANAGED_GPU_E2E_OK` and JSON showing epoch=2, two distinct PIDs, v1/v2 replies, global CUDA free-memory samples and exit=0.
Hard cap 16..64 MiB per worker, with >=512 MiB free headroom and at most one owned CUDA worker during transition. If proof cannot be established, fail closed; do not relax VRAM thresholds.

## Limitations / promotion no-go

- This is a **narrow synchronous str/bytes pilot**, not a general managed plugin or streaming/audio RPC API.
- B.1 close is nonblocking and may return with leased work still alive. B.2 stop-first can have service downtime and a terminal FAILED resource fence.
- `ExecutionContext.close()` and `PluginKernel.shutdown_force()` may suppress teardown errors. The exclusive resource probe must independently establish worker exit and observable memory recovery.
- WDDM memory statistics are global and noisy; do not call one 32-MiB run a guarantee for a real 8-GB LLM swap.
- No transitive source/weight hashing, process-wide GPU admission arbiter, mixed DependencyScope, async generator leases, permission upgrade, stream continuations, idempotent RPC replay or model state migration.
- Do not use this sidecar to silently hot-swap a legacy raw PluginService.get() reference. A future production owner path needs explicit opt-in and a wider baseline test gate.

Gate C.1 should be classified as **research PASS** only after BOTH the new scoped Windows test results and the optional native GPU E2E are confirmed, not merely because earlier Gate B was green.


## Windows completion evidence (2026-10-13)

- User reports full Gate C.1 scoped CPU regression set green (96 methods expected).
- Explicit native CUDA managed-port E2E marker: `GATE_C1_MANAGED_GPU_E2E_OK`.
- Reported exit code: `GATE_C1_GPU_EXIT=0`.
- Epoch 2, calls `1.0.0:first` and `2.0.0:second`.
- Worker PIDs `14272` -> `8496`, at 32 MiB requested allocation each.
- GPU 12226 MiB total; initial/free after shutdown 6878 / 6878 MiB.
- During v1, observed global free-memory reduction 172 MiB (context overhead included).

This confirms the research-only synchronous managed-invoke path on this
machine. It does not certify per-process VRAM accounting, model-weight
integrity, streaming, mixed scopes, failure-rate targets or production
compatibility. The next separate Gate C.2 experiments with explicit
weight/config file SHA256 pinning before exclusive resource teardown.
