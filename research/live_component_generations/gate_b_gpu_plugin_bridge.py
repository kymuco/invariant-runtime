"""Gate B.4 research E2E: track only CUDA subprocess handles launched by a
real Invariant plugin fixture. This is host observation, not a GPU scheduler.

Single-threaded research test only. There is NO process-wide ownership
guarantee; native CUDA device free memory is checked independently.
"""
from __future__ import annotations

from gate_b_cuda_windows_smoke import launch_cuda_worker

owned_workers = []


def start_worker(revision: str, device: int, mib: int):
    worker = launch_cuda_worker(revision, device, mib)
    owned_workers.append(worker)
    return worker


def all_reaped() -> bool:
    return all(worker.proc.poll() is not None for worker in owned_workers)


def cleanup_owned() -> None:
    errors = []
    for worker in owned_workers:
        if worker.proc.poll() is None:
            try:
                worker.stop(graceful_timeout=1, escalation_timeout=2)
            except Exception as exc:
                errors.append((worker.pid, repr(exc)))
    if errors or not all_reaped():
        raise RuntimeError(f"CUDA fixture workers not fully reaped: {errors!r}")
