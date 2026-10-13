"""Gate B.4: real Invariant plugin lifecycle controlling a native CUDA worker.

Explicit opt-in Windows NVIDIA research experiment, 32 MiB allocation per
child by default (hard max 64), NEVER run during unittest discovery.

Reuses the existing Gate B.2 RealStopFirstHost and Gate B.3 native CUDA
worker. Creates two temporary manifest-backed plugin versions. Plugin
initialize() starts a real CUDA subprocess; plugin shutdown() stops/reaps
it. The host separately verifies original worker exit AND global device
memory recovery before starting the next plugin. No duplicated scheduler.

This verifies one synchronous EXECUTION-scope plugin service contract,
not production hot reload, actual LLM weights or state continuity.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from gate_b_cuda_windows_smoke import (
    CudaDriver,
    MIB,
    memory_drop_observed,
    memory_recovered,
)
from gate_b_exclusive_host import RealStopFirstHost
import gate_b_gpu_plugin_bridge as ledger
from stop_first import Phase


def write_version(root: Path, version: str, device: int, mib: int) -> None:
    target = root / ("gpu-" + version)
    target.mkdir()
    # Minimal real BasePlugin, with no side-channel model loader or dummy
    # initialize: it launches/owns a real CUDA process and validates RPCs.
    source = f"""from invariant.spec import BasePlugin


class GPUPlugin(BasePlugin):
    def initialize(self, **deps):
        from gate_b_gpu_plugin_bridge import start_worker
        self.worker = start_worker({version!r}, {device}, {mib})

    def shutdown(self):
        if hasattr(self, "worker"):
            self.worker.stop(graceful_timeout=1.5, escalation_timeout=2)

    def synthesize(self, text):
        response = self.worker.request("ping", timeout=3)
        if (response.get("kind") != "pong"
                or response.get("pid") != self.worker.pid
                or response.get("revision") != {version!r}):
            raise RuntimeError("CUDA worker RPC identity drift")
        return {version!r} + ":" + text
"""
    (target / "plugin.py").write_text(source, encoding="utf-8")
    (target / "manifest.json").write_text(
        json.dumps({
            "manifest_version": 1,
            "id": "research:gpu_provider",
            "version": version,
            "source": {"code_path": "plugin.py", "class_name": "GPUPlugin"},
        }), encoding="utf-8",
    )


def run(device: int, mib: int, min_headroom_mib: int) -> dict:
    if not 16 <= mib <= 64:
        raise ValueError("allocation cap: 16..64 MiB")
    if min_headroom_mib < 512:
        raise ValueError("minimum GPU headroom cannot be below 512 MiB")
    if ledger.owned_workers:
        raise RuntimeError("CUDA worker ledger not empty at start")

    monitor = CudaDriver(device)
    host = None
    cleanup_errors: list[str] = []
    try:
        baseline, total = monitor.free_bytes()
        if baseline < min_headroom_mib * MIB:
            raise RuntimeError(
                f"Insufficient free device memory: {baseline // MIB} MiB; "
                f"requires {min_headroom_mib} MiB; no worker started"
            )
        tolerance = 8 * MIB

        def free_for_exclusive_start() -> bool:
            if not ledger.all_reaped():
                return False
            observed, _ = monitor.free_bytes()
            return memory_recovered(
                baseline, observed,
                all_owned_workers_exited=True,
                tolerance_bytes=tolerance,
            )

        with tempfile.TemporaryDirectory(prefix="invariant-gpu-plugin-e2e-") as name:
            root = Path(name)
            write_version(root, "1.0.0", device, mib)
            host = RealStopFirstHost(
                [root], root_ref="research:gpu_provider",
                is_resource_free=free_for_exclusive_start,
            )

            old_result = host.call("first")
            if old_result != "1.0.0:first":
                raise RuntimeError(f"v1 plugin route invalid: {old_result!r}")
            if len(ledger.owned_workers) != 1:
                raise RuntimeError("expected exactly one real CUDA child after v1")
            old = ledger.owned_workers[0]
            pid1 = old.pid
            if old.proc.poll() is not None:
                raise RuntimeError("v1 CUDA worker exited unexpectedly")
            in_use_free, _ = monitor.free_bytes()
            drop = baseline - in_use_free
            if not memory_drop_observed(baseline, in_use_free, mib * MIB):
                raise RuntimeError(
                    f"INCONCLUSIVE: v1 CUDA buffer not independently observed; "
                    f"global free-memory drop={drop // MIB} MiB"
                )

            # Descriptor-only staging of v2 — no second worker exists here.
            write_version(root, "2.0.0", device, mib)
            stage = host.stage()
            if len(ledger.owned_workers) != 1 or old.proc.poll() is not None:
                raise RuntimeError("staging unexpectedly started/stopped CUDA worker")
            new_view = host.replace(
                stage, drain_timeout_s=3.0, release_timeout_s=8.0
            )
            if new_view.epoch != 2:
                raise RuntimeError("exclusive catalog did not advance to epoch 2")
            if len(ledger.owned_workers) != 2:
                raise RuntimeError("expected exactly two total CUDA child launches")
            successor = ledger.owned_workers[1]
            pid2 = successor.pid
            if pid1 == pid2 or old.proc.poll() is None:
                raise RuntimeError("old process not confirmed reaped before v2 use")
            if successor.proc.poll() is not None:
                raise RuntimeError("new CUDA worker exited after activation")
            if host.call("second") != "2.0.0:second":
                raise RuntimeError("new Invariant plugin/worker route invalid")
            if host.status().phase is not Phase.ACTIVE:
                raise RuntimeError("exclusive route not ACTIVE after publish")
            host.close(drain_timeout_s=3, release_timeout_s=8)
            if successor.proc.poll() is None or not ledger.all_reaped():
                raise RuntimeError("one or more CUDA workers survived final close")
            ending_free, _ = monitor.free_bytes()
            if not memory_recovered(
                baseline, ending_free,
                all_owned_workers_exited=ledger.all_reaped(),
                tolerance_bytes=tolerance,
            ):
                raise RuntimeError(
                    f"INCONCLUSIVE: GPU memory not recovered after plugin close; "
                    f"baseline={baseline // MIB} MiB, final={ending_free // MIB} MiB"
                )
            return {
                "worker_pids": [pid1, pid2],
                "catalog_epoch": new_view.epoch,
                "v1_result": old_result,
                "v2_result": "2.0.0:second",
                "allocated_mib_per_worker": mib,
                "baseline_free_mib": baseline // MIB,
                "observed_v1_drop_mib": drop // MIB,
                "end_free_mib": ending_free // MIB,
                "gpu_total_mib": total // MIB,
                "proof_scope": "real_invariant_plugin_to_real_cuda_worker_stop_first",
            }
    finally:
        if host is not None:
            try:
                host.close(drain_timeout_s=0, release_timeout_s=2)
            except BaseException as exc:
                cleanup_errors.append(f"host: {exc!r}")
        try:
            ledger.cleanup_owned()
        except BaseException as exc:
            cleanup_errors.append(f"owned CUDA children: {exc!r}")
        try:
            monitor.close()
        except BaseException as exc:
            cleanup_errors.append(f"GPU monitor: {exc!r}")
        if cleanup_errors:
            raise RuntimeError(
                "GPU test cleanup incomplete: " + "; ".join(cleanup_errors)
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--mib", type=int, default=32)
    parser.add_argument("--min-headroom-mib", type=int, default=512)
    args = parser.parse_args()
    try:
        evidence = run(args.device, args.mib, args.min_headroom_mib)
    except BaseException as exc:
        print("GATE_B4_GPU_PLUGIN_NOT_PROVEN: " + repr(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    print("GATE_B4_GPU_PLUGIN_E2E_OK")
    print(json.dumps(evidence, sort_keys=True))
