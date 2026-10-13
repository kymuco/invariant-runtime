# Gate B.4 — real Invariant plugin managing a native CUDA worker

**Status:** research E2E authored, **NOT YET RUN** on the user's Windows GPU. No production `invariant/` or main changes.

## Previous verified results

Gate B.3 Windows on RTX 5070, 2026-10-13:

- 13/13 CPU-only evidence tests PASS.
- Native worker replacement emitted `GATE_B3_NATIVE_GPU_SMOKE_OK`, exit=0.
- 32 MiB buffer per process, total device memory 12226 MiB.
- Initial free 6753 MiB, v1 drop 172 MiB, ending free 6756 MiB.
- Distinct real worker PIDs: 24956 then 3052.

This validates sequential native CUDA worker launch/termination and independent GLOBAL CUDA free-memory observation; it does not prove per-PID accounting or real LLM reload.

## Scope of B.4

Connect the **already tested** pieces without another scheduler:

1. `gate_b_gpu_plugin_e2e.py` generates two real manifest-backed Invariant `BasePlugin` versions in a temporary directory.
2. Each plugin's real `initialize()` calls `gate_b_gpu_plugin_bridge.start_worker()` to create a **separate real CUDA process** holding a touched, bounded 32 MiB device buffer.
3. Its `synthesize()` performs a real `ping` RPC with revision and PID checks. Its `shutdown()` stops and reaps that child.
4. The existing `RealStopFirstHost` performs actual Invariant `ExecutionContext` / plugin activation with a single `StopFirstSlot`.
5. The host's resource-free callback needs BOTH all tracked worker subprocesses reaped AND device free-memory returned within the unchanged 8-MiB tolerance.
6. `stage()` is metadata-only. `replace()` must stop v1 before starting v2. At the actual plugin-worker launch boundary, the independent fixture ledger also rejects any overlap.
7. After v2 becomes active, a second real plugin RPC is checked. Finally, host `close()` must reap v2 and verify global GPU free-memory recovery.

Only a one-root synchronous method with EXECUTION scope is in scope. This is not production hot swapping, GPU capacity allocation, a real 8GB LLM, long-running streaming inference, user state migration, process-wide GPU ownership, or exact WDDM per-PID accounting.

## Windows run (hardware explicitly opt-in)

Run with the same validated RTX 5070 environment. One worker at a time; each child may allocate 16..64 MiB maximum. No toolchain changes or external model downloads.

```powershell
Set-Location W:\dev\invariant-runtime
git pull --ff-only origin research/catalog-snapshot-gate-a-2026-10-11

$python = '.\.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

& $python research/live_component_generations/gate_b_gpu_plugin_e2e.py --device 0 --mib 32
Write-Host "GATE_B4_EXIT=$LASTEXITCODE"
```

PASS marker: `GATE_B4_GPU_PLUGIN_E2E_OK`, followed by JSON reporting v1/v2 worker PIDs, exact plugin call results, final catalog epoch 2, CUDA free memory before/during/after, allocation per worker and proof scope. A success marker without exit code 0 or explicit memory/process checks is NOT sufficient.

If it fails, retain the complete `GATE_B4_GPU_PLUGIN_NOT_PROVEN` error; do not weaken VRAM thresholds or modify production to force green. Source files are not immutable across transitive imports and weights. This is a single run, not a stochastic reliability proof.

**Next decision:** a successful B.4 run supports drafting a small production-facing owner contract for sync GPU workers. Real process supervision, crash fencing, async leases, external resource monitor, model artifact identity, and user session state transfer remain separate blockers.
