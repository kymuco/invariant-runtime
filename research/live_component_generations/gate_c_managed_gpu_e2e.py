"""Gate C.1: optional real GPU E2E through the managed INVOKE boundary.

Reuses B.4 fixture, B.3 CUDA driver and B.2 real stop-first owner. No
second scheduler or registry, and no new production PluginService API.
Requires explicit execution on Windows; never discovered by unittest.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import gate_b_gpu_plugin_bridge as ledger
from gate_b_cuda_windows_smoke import (
    CudaDriver,
    MIB,
    memory_drop_observed,
    memory_recovered,
)
from gate_b_gpu_plugin_e2e import write_version
from gate_c_managed_call import ManagedCallPort


def run(*, device: int, mib: int, min_headroom_mib: int) -> dict:
    if not 16 <= mib <= 64:
        raise ValueError("CUDA worker limit: 16..64 MiB")
    if min_headroom_mib < 512:
        raise ValueError("CUDA headroom must be >=512 MiB")
    if ledger.owned_workers:
        raise RuntimeError("fixture already owns CUDA workers")

    monitor = CudaDriver(device)
    port = None
    errors = []
    try:
        baseline, total = monitor.free_bytes()
        if baseline < min_headroom_mib * MIB:
            raise RuntimeError("insufficient free GPU memory; no worker started")

        def released() -> bool:
            if not ledger.all_reaped():
                return False
            free, _ = monitor.free_bytes()
            return memory_recovered(
                baseline, free, all_owned_workers_exited=True,
                tolerance_bytes=8 * MIB,
            )

        with tempfile.TemporaryDirectory(prefix="invariant-c1-gpu-") as temp:
            root = Path(temp)
            write_version(root, "1.0.0", device, mib)
            port = ManagedCallPort.exclusive(
                [root], root_ref="research:gpu_provider",
                is_resource_free=released,
            )
            old_reply = port.invoke("first")
            if old_reply != "1.0.0:first" or len(ledger.owned_workers) != 1:
                raise RuntimeError("managed v1 invocation/worker identity invalid")
            old = ledger.owned_workers[0]
            pid1 = old.pid
            if old.proc.poll() is not None:
                raise RuntimeError("v1 worker died prematurely")
            during, _ = monitor.free_bytes()
            if not memory_drop_observed(baseline, during, mib * MIB):
                raise RuntimeError(
                    "INCONCLUSIVE: no measurable CUDA memory allocation for v1"
                )

            # No extra worker during metadata staging: upgrade is delegated
            # through the single existing exclusive owner, not a new router.
            write_version(root, "2.0.0", device, mib)
            epoch = port.upgrade(drain_timeout_s=3, release_timeout_s=8)
            if epoch != 2 or len(ledger.owned_workers) != 2:
                raise RuntimeError("managed route was not upgraded once")
            new = ledger.owned_workers[1]
            pid2 = new.pid
            if old.proc.poll() is None or new.proc.poll() is not None or pid1 == pid2:
                raise RuntimeError("old worker not reaped before new worker ready")
            new_reply = port.invoke("second")
            if new_reply != "2.0.0:second":
                raise RuntimeError("managed v2 call returned wrong generation")

            port.close(drain_timeout_s=3, release_timeout_s=8)
            if not ledger.all_reaped():
                raise RuntimeError("CUDA worker survived managed port.close")
            end_free, _ = monitor.free_bytes()
            if not memory_recovered(
                baseline, end_free, all_owned_workers_exited=True,
                tolerance_bytes=8 * MIB,
            ):
                raise RuntimeError("INCONCLUSIVE: post-close VRAM not recovered")
            return {
                "catalog_epoch": epoch,
                "v1_result": old_reply,
                "v2_result": new_reply,
                "worker_pids": [pid1, pid2],
                "allocated_mib_per_worker": mib,
                "baseline_free_mib": baseline // MIB,
                "v1_observed_drop_mib": (baseline - during) // MIB,
                "end_free_mib": end_free // MIB,
                "gpu_total_mib": total // MIB,
                "proof_scope": "managed_invoke_real_plugin_real_cuda_stop_first",
            }
    finally:
        if port is not None:
            try:
                port.close(drain_timeout_s=0, release_timeout_s=2)
            except BaseException as exc:
                errors.append(f"port={exc!r}")
        try:
            ledger.cleanup_owned()
        except BaseException as exc:
            errors.append(f"worker={exc!r}")
        try:
            monitor.close()
        except BaseException as exc:
            errors.append(f"monitor={exc!r}")
        if errors:
            raise RuntimeError(
                "GPU-managed invocation cleanup not confirmed: " + "; ".join(errors)
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--mib", type=int, default=32)
    parser.add_argument("--min-headroom-mib", type=int, default=512)
    args = parser.parse_args()
    try:
        evidence = run(
            device=args.device, mib=args.mib,
            min_headroom_mib=args.min_headroom_mib,
        )
    except BaseException as exc:
        print("GATE_C1_MANAGED_GPU_NOT_PROVEN: " + repr(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    print("GATE_C1_MANAGED_GPU_E2E_OK")
    print(json.dumps(evidence, sort_keys=True))
