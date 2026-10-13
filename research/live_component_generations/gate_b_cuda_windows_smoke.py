"""Native NVIDIA CUDA process/VRAM smoke: research-only, Windows-only.

Run explicitly; NEVER auto-run from unit tests. Uses at most 64 MiB of
CUDA device allocations per worker, with only ONE worker alive at a time.
StopFirstSlot controls route admission. This does not validate any specific
LLM or Python Invariant PluginService integration (covered separately in
Gate B.2) or certify driver-wide VRAM reclamation.

The CudaDriver uses the CUDA Driver API bundled with installed GPU drivers;
no PyTorch/driver installation, compiler, nvidia-smi or network required.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import queue
import subprocess
import sys
import time
from pathlib import Path
from threading import Thread

from prototype import ProviderSpec
from stop_first import Phase, StopFirstSlot
from worker_supervisor import WorkerHandle

MIB = 1024 * 1024


def memory_drop_observed(baseline_free: int, active_free: int, allocated: int) -> bool:
    """Require a meaningful independent physical-memory observation."""
    if not all(isinstance(x, int) and x >= 0 for x in
               (baseline_free, active_free, allocated)):
        return False
    return allocated >= 16 * MIB and baseline_free - active_free >= allocated // 2


def memory_recovered(
    baseline_free: int, after_free: int | None, *,
    all_owned_workers_exited: bool, tolerance_bytes: int,
) -> bool:
    """Conservative global CUDA free-memory check; not per-PID VRAM proof."""
    if not all_owned_workers_exited or not isinstance(after_free, int):
        return False
    if baseline_free < 0 or after_free < 0 or tolerance_bytes < 0:
        return False
    return after_free >= baseline_free - tolerance_bytes


class CudaUnavailable(RuntimeError):
    pass


class CudaDriver:
    """Tiny Windows CUDA Driver API adapter. One current context per thread."""

    def __init__(self, index: int):
        if sys.platform != "win32":
            raise CudaUnavailable("Windows NVIDIA CUDA driver required")
        try:
            self.dll = ctypes.WinDLL("nvcuda.dll")
        except OSError as exc:
            raise CudaUnavailable("nvcuda.dll not present") from exc
        self._load("cuInit", [ctypes.c_uint])
        self._load("cuDeviceGet", [ctypes.POINTER(ctypes.c_int), ctypes.c_int])
        self._load(
            "cuDevicePrimaryCtxRetain",
            [ctypes.POINTER(ctypes.c_void_p), ctypes.c_int],
        )
        self._load("cuCtxSetCurrent", [ctypes.c_void_p])
        self._load(
            "cuMemGetInfo_v2",
            [ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)],
        )
        self._load(
            "cuMemAlloc_v2",
            [ctypes.POINTER(ctypes.c_uint64), ctypes.c_size_t],
        )
        self._load(
            "cuMemsetD8_v2",
            [ctypes.c_uint64, ctypes.c_ubyte, ctypes.c_size_t],
        )
        self._load("cuMemFree_v2", [ctypes.c_uint64])
        self._load("cuDevicePrimaryCtxRelease", [ctypes.c_int])
        self._check("cuInit", 0)
        device = ctypes.c_int()
        self._check("cuDeviceGet", ctypes.byref(device), index)
        self.device = int(device.value)
        self.context = ctypes.c_void_p()
        self.allocation = ctypes.c_uint64(0)
        self._check("cuDevicePrimaryCtxRetain", ctypes.byref(self.context), self.device)
        try:
            self._check("cuCtxSetCurrent", self.context)
        except BaseException:
            self.dll.cuDevicePrimaryCtxRelease(self.device)
            raise

    def _load(self, name: str, argtypes: list):
        try:
            fn = getattr(self.dll, name)
        except AttributeError as exc:
            raise CudaUnavailable(f"CUDA driver symbol missing: {name}") from exc
        fn.argtypes = argtypes
        fn.restype = ctypes.c_int

    def _check(self, name: str, *args):
        code = getattr(self.dll, name)(*args)
        if code:
            raise CudaUnavailable(f"{name} returned CUDA error {code}")

    def free_bytes(self) -> tuple[int, int]:
        self._check("cuCtxSetCurrent", self.context)
        free = ctypes.c_size_t()
        total = ctypes.c_size_t()
        self._check("cuMemGetInfo_v2", ctypes.byref(free), ctypes.byref(total))
        return free.value, total.value

    def allocate(self, mib: int) -> None:
        if not 16 <= mib <= 64:
            raise ValueError("GPU test allocation must be between 16 and 64 MiB")
        if self.allocation.value:
            raise RuntimeError("already allocated")
        self._check("cuCtxSetCurrent", self.context)
        self._check("cuMemAlloc_v2", ctypes.byref(self.allocation), mib * MIB)
        try:
            # Force device residency instead of relying on virtual reservation.
            self._check("cuMemsetD8_v2", self.allocation, 0xA5, mib * MIB)
        except BaseException:
            self.dll.cuMemFree_v2(self.allocation)
            self.allocation = ctypes.c_uint64(0)
            raise

    def close(self) -> None:
        if not getattr(self, "context", None) or not self.context.value:
            return
        try:
            self._check("cuCtxSetCurrent", self.context)
            if self.allocation.value:
                self._check("cuMemFree_v2", self.allocation)
                self.allocation = ctypes.c_uint64(0)
        finally:
            self._check("cuDevicePrimaryCtxRelease", self.device)
            self.context = ctypes.c_void_p()


def emit(**data):
    sys.stdout.write(json.dumps(data, sort_keys=True) + "\n")
    sys.stdout.flush()


def worker_main(revision: str, device: int, mib: int) -> int:
    driver = None
    try:
        driver = CudaDriver(device)
        driver.allocate(mib)
        free, total = driver.free_bytes()
        emit(kind="ready", revision=revision, pid=os.getpid(), mib=mib,
             cuda_free_bytes=free, cuda_total_bytes=total)
        for raw in sys.stdin:
            request = json.loads(raw)
            if request.get("op") == "ping":
                emit(kind="pong", revision=revision, pid=os.getpid())
            elif request.get("op") == "stop":
                driver.close()
                driver = None
                emit(kind="stopping", pid=os.getpid())
                return 0
            else:
                emit(kind="error", error="unknown command")
        return 0
    except BaseException as exc:
        emit(kind="error", error=repr(exc), pid=os.getpid())
        return 11
    finally:
        if driver is not None:
            try:
                driver.close()
            except BaseException:
                pass


def launch_cuda_worker(revision: str, device: int, mib: int) -> WorkerHandle:
    script = Path(__file__).resolve()
    # Pin exact source bytes for the duration of this launch.
    pinned = hashlib.sha256(script.read_bytes()).hexdigest()
    if hashlib.sha256(script.read_bytes()).hexdigest() != pinned:
        raise RuntimeError("CUDA worker source changed before spawn")
    proc = subprocess.Popen(
        [sys.executable, "-u", str(script), "--worker",
         "--revision", revision, "--device", str(device), "--mib", str(mib)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, encoding="utf-8", text=True, bufsize=1,
        cwd=str(script.parent),
    )
    output: queue.Queue[dict | None] = queue.Queue()

    def reader():
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                try:
                    output.put(json.loads(line))
                except (TypeError, ValueError):
                    output.put({"kind": "error", "error": "invalid worker JSON"})
        finally:
            output.put(None)
            proc.stdout.close()

    Thread(target=reader, daemon=True, name=f"cuda-research-{proc.pid}").start()
    handle = WorkerHandle(proc, output, revision=revision)
    try:
        msg = handle.wait_message(timeout=12)
        if (
            msg.get("kind") != "ready"
            or msg.get("pid") != proc.pid
            or msg.get("revision") != revision
            or msg.get("mib") != mib
            or proc.poll() is not None
        ):
            raise RuntimeError(f"CUDA worker not READY: {msg!r}")
        return handle
    except BaseException:
        # No orphan GPU allocation, including failed partial startup.
        try:
            handle.stop(graceful_timeout=1, escalation_timeout=2)
        except BaseException as exc:
            raise RuntimeError(
                "CUDA worker cleanup unproven; investigate child PID "
                + str(proc.pid)
            ) from exc
        raise


def smoke(device: int, mib: int, min_headroom_mib: int) -> None:
    if not 16 <= mib <= 64:
        raise ValueError("--mib must be between 16 and 64 (safe upper cap)")
    if min_headroom_mib < 512:
        raise ValueError("--min-headroom-mib cannot be below 512")
    monitor = CudaDriver(device)
    handles: list[WorkerHandle] = []
    try:
        baseline, total = monitor.free_bytes()
        if baseline < min_headroom_mib * MIB:
            raise RuntimeError(
                f"GPU headroom too low: free={baseline // MIB} MiB; "
                f"require={min_headroom_mib} MiB. No worker was launched."
            )

        # CUDA WDDM memory figures are global and noisy: this is a
        # conservative observation, NOT per-PID accounting or a proof
        # that any unrelated CUDA process is gone.
        tolerance = 8 * MIB

        def released():
            if any(h.proc.poll() is None for h in handles):
                return False
            free, _ = monitor.free_bytes()
            return memory_recovered(
                baseline, free,
                all_owned_workers_exited=True, tolerance_bytes=tolerance,
            )

        slot = StopFirstSlot("research.cuda.real-gpu", is_resource_free=released)

        def add(revision: str):
            def start():
                handle = launch_cuda_worker(revision, device, mib)
                handles.append(handle)
                return handle

            def stop(handle: WorkerHandle):
                handle.stop(graceful_timeout=1.5, escalation_timeout=2)
                if handle.proc.poll() is None:
                    raise RuntimeError("process termination not observed")

            slot.install(ProviderSpec(
                slot.contract, "research:cuda", revision, start, stop
            ))

        add("v1")
        add("v2")
        slot.replace("research:cuda", "v1", release_timeout_s=8)
        with slot.acquire() as first:
            pid1 = first.pid
            if first.request("ping", timeout=3).get("revision") != "v1":
                raise RuntimeError("v1 CUDA worker RPC invalid")
            during, _ = monitor.free_bytes()
            drop = baseline - during
            if not memory_drop_observed(baseline, during, mib * MIB):
                raise RuntimeError(
                    f"INCONCLUSIVE: allocated {mib} MiB in child but global "
                    f"CUDA free-memory drop was only {drop // MIB} MiB. "
                    "This platform cannot supply enough independent evidence."
                )

        slot.replace("research:cuda", "v2", release_timeout_s=8)
        with slot.acquire() as second:
            pid2 = second.pid
            if pid2 == pid1 or second.request("ping", timeout=3).get("revision") != "v2":
                raise RuntimeError("v2 worker routing/PID evidence invalid")
            if handles[0].proc.poll() is None:
                raise RuntimeError("v1 process still alive during v2 allocation")
        slot.close(drain_timeout_s=2, release_timeout_s=8)
        final_free, _ = monitor.free_bytes()
        if not released() or slot.snapshot().phase is not Phase.EMPTY:
            raise RuntimeError("INCONCLUSIVE: CUDA release was not observed")
        print("GATE_B3_NATIVE_GPU_SMOKE_OK")
        print(json.dumps({
            "device_index": device,
            "allocated_mib_per_worker": mib,
            "worker_pids": [pid1, pid2],
            "baseline_free_mib": baseline // MIB,
            "v1_observed_drop_mib": drop // MIB,
            "end_free_mib": final_free // MIB,
            "gpu_total_mib": total // MIB,
            "proof_scope": "worker_reaped_plus_global_cuda_memory_recovery",
        }, sort_keys=True))
    finally:
        # Best-effort cleanup even on proof failure. Do not report success
        # if any subprocess cannot be stopped/reaped.
        for handle in handles:
            if handle.proc.poll() is None:
                handle.stop(graceful_timeout=1, escalation_timeout=2)
        monitor.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--revision", default="")
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--mib", type=int, default=32)
    parser.add_argument("--min-headroom-mib", type=int, default=512)
    args = parser.parse_args()
    if args.worker:
        raise SystemExit(worker_main(args.revision, args.device, args.mib))
    try:
        smoke(args.device, args.mib, args.min_headroom_mib)
    except BaseException as exc:
        print("GATE_B3_NATIVE_GPU_SMOKE_NOT_PROVEN: " + repr(exc), file=sys.stderr)
        raise SystemExit(1) from exc
