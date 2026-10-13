# Gate B.3 — bounded native CUDA worker smoke (Windows research)

**State: implementation authored, native GPU test NOT YET RUN.**
New CPU-only evidence policy tests also need a Windows checkout run.
Prior Gate B.2: 73 scoped tests reported PASS by user (2026-10-13).

No production files, public API or main were changed.

## Why native CUDA instead of an NVIDIA-SMI-only proof?

Under Windows WDDM, NVIDIA documents per-process NVML
`usedGpuMemory` as `NVML_VALUE_NOT_AVAILABLE`. Thus a user-visible
nvidia-smi process table is not an adequate per-PID VRAM-release oracle.

This experiment uses NVIDIA's CUDA Driver API in a **separate owned Python
worker** (the nvcuda.dll installed by the NVIDIA driver, no CUDA toolkit
or third-party package required). The child allocates a small, bounded
CUDA device buffer and explicitly touches it using cuMemsetD8_v2.
The parent uses its OWN CUDA context to sample device free-memory
and holds an actual process handle. It requires:

1. A successful native cuMemAlloc_v2/cuda memset and child READY;
2. A measurable drop in global CUDA free-memory (at least half of the
   requested 32 MiB default allocation);
3. The old child process has genuinely exited **before** v2 can start;
4. Global CUDA free-memory has returned to near the parent-context
   baseline (within 8 MiB tolerance);
5. Both children have distinct PID/revision replies, old first and new
   second, and after terminal slot close the final owned worker is
   reaped and memory recovery checked again.

All start/stop routing uses the **existing** `StopFirstSlot` from Gate
B.2 and `WorkerHandle` from the existing subprocess supervisor. No new
scheduler or independent route cache.

**Safety defaults:** allocate 32 MiB in each (never simultaneous) child;
--mib accepts only 16..64; require at least 512 MiB free before starting
any child. All owned subprocesses are stopped/reaped in a finally block.
This is an explicit opt-in script and is NEVER executed merely by
unittest discovery.

## Gate B.3 scientific limitation

CUDA free bytes are GLOBAL system measurements. Other applications may
allocate/free memory concurrently; WDDM can add allocator/context
accounting noise. The script therefore fails conservatively when
observed drop/recovery is too ambiguous. A green result is **two
corroborating observations** (owner process lifecycle + memory recovery
on device), **NOT** a formal proof that every byte of one PID's VRAM
has been released, nor that a real 8GB LLM can fit after a swap.

The host-owned resource policy in production still requires a trustworthy
device/process identity, exclusive admission across multiple hosts,
worker termination/liveness and proper idle workers, along with a way to
preserve user session state. Do not conflate driver memory snapshots
with tracked VRAM ownership.

## Execution

1. CPU-only checks (6 methods), safe even if no GPU is present:

~~~powershell
Set-Location W:\dev\invariant-runtime
git fetch origin
git switch research/catalog-snapshot-gate-a-2026-10-11
git pull --ff-only origin research/catalog-snapshot-gate-a-2026-10-11

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
& $python -m unittest discover -s research/live_component_generations -p "test_gate_b_cuda_evidence.py" -v
if ($LASTEXITCODE -ne 0) { throw "GATE_B3_CPU_EVIDENCE_FAILED" }
~~~

2. Explicit small, real GPU experiment (not an ordinary unittest).
This can fail with NOT_PROVEN due to unsupported CUDA symbols, no
NVIDIA hardware, contention, noise or actual leaked memory; do not
weaken thresholds merely to make the check pass.

~~~powershell
& $python research/live_component_generations/gate_b_cuda_windows_smoke.py --device 0 --mib 32
if ($LASTEXITCODE -ne 0) { throw "GATE_B3_NATIVE_GPU_NOT_PROVEN" }
~~~

Expected success marker:
`GATE_B3_NATIVE_GPU_SMOKE_OK`, followed by JSON with worker PIDs,
allocation, device index, initial free memory, active observed delta,
and end free memory.

If it fails, send the exact `GATE_B3_NATIVE_GPU_SMOKE_NOT_PROVEN`
line and any traceback/error. Investigate the driver signature or
measurement without raising the VRAM limit or changing production code.
Do not run while available VRAM is very low or GPU is unstable.

3. Keep older B.2 73 tests as a separate regression gate. The 6
CPU-only checks plus the native smoke are **not** part of the prior
73-test evidence.

## References

- NVIDIA CUDA FAQ: Driver API only requires nvcuda.dll on Windows:
  https://developer.nvidia.com/cuda/faq
- NVIDIA CUDA Driver API (context, memory allocation, memset):
  https://docs.nvidia.com/cuda/cuda-driver-api/cuda_driver_api/
- NVIDIA NVML process info, WDDM per-PID memory limitation:
  https://docs.nvidia.com/deploy/nvml-api/latest/api/structnvmlProcessInfo__t.html

## Promotion boundary

Still **NO-GO** for production GPU hot swap after a native smoke PASS.
Need a host-owned resource monitor and worker-identity contract, full
OS-level release evidence appropriate to target hardware, operational
crash/recovery policy, full baseline tests, and a no-raw-ref API
guarantee. The experiment uses a tiny CUDA buffer and no LLM weights.

## Windows incident 2026-10-13: first CUDA child did not start

Observed on user's Windows checkout at e72114385c3152763f3b0fbd6291022987056890:

- 6/6 CPU-only evidence tests: PASS.
- Native process smoke: CandidateFailed('candidate startup failed; restored=False').
- This occurred during initial v1 worker launch. No successful v1->v2 replacement or physical VRAM release was proved.
- The wrapper was hiding the worker/driver underlying failure; do not assume CUDA or VRAM shortage from this message alone.

Gate B.3 has been amended to display its full chained cause, and two
CPU-only tests were added for this diagnostic behavior (8 total).
Hardware proof remains NOT PROVEN until the full native smoke succeeds.

### One-worker diagnostic, independent of StopFirstSlot

Run after pulling the updated research branch; this is a 32 MiB single
worker allocation and immediately requests a clean shutdown:

~~~powershell
Set-Location W:\dev\invariant-runtime
git pull --ff-only origin research/catalog-snapshot-gate-a-2026-10-11
$python = '.\.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }
'{"op":"stop"}' | & $python research/live_component_generations/gate_b_cuda_windows_smoke.py --worker --revision diag --device 0 --mib 32
~~~

Expected from the child: one JSON 'ready' record and one 'stopping' record;
if JSON kind='error', its error field is the real CUDA diagnostic.
Exit code must be zero. A successful direct worker run is NOT Gate B.3 PASS.

Then, if appropriate, retry the same unchanged full smoke:

~~~powershell
& $python research/live_component_generations/gate_b_cuda_windows_smoke.py --device 0 --mib 32
~~~

No VRAM thresholds were reduced. Please capture the actual error chain if it fails.
