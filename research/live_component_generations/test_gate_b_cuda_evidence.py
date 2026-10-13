"""Gate B.3: CPU-only fail-closed evidence policy checks.

These deliberately DO NOT launch CUDA or allocate GPU memory. Native GPU
proof is a separate explicit script that fails if CUDA evidence is missing.
"""
from __future__ import annotations

import unittest

from gate_b_cuda_windows_smoke import (
    MIB,
    memory_drop_observed,
    memory_recovered,
)


class NativeCudaEvidencePolicyTests(unittest.TestCase):
    def test_real_allocation_needs_visible_memory_delta(self):
        self.assertTrue(memory_drop_observed(2048 * MIB, 1980 * MIB, 32 * MIB))
        self.assertFalse(memory_drop_observed(2048 * MIB, 2046 * MIB, 32 * MIB))

    def test_free_memory_recovery_and_reaped_worker_both_required(self):
        self.assertTrue(memory_recovered(
            2048 * MIB, 2045 * MIB,
            all_owned_workers_exited=True, tolerance_bytes=8 * MIB,
        ))
        self.assertFalse(memory_recovered(
            2048 * MIB, 2045 * MIB,
            all_owned_workers_exited=False, tolerance_bytes=8 * MIB,
        ))
        self.assertFalse(memory_recovered(
            2048 * MIB, 1999 * MIB,
            all_owned_workers_exited=True, tolerance_bytes=8 * MIB,
        ))

    def test_unavailable_readout_cannot_be_treated_as_release_proof(self):
        self.assertFalse(memory_recovered(
            2048 * MIB, None,
            all_owned_workers_exited=True, tolerance_bytes=8 * MIB,
        ))

    def test_no_negative_sentinel_or_invalid_allocation_can_pass(self):
        self.assertFalse(memory_recovered(
            -1, 2048 * MIB,
            all_owned_workers_exited=True, tolerance_bytes=8 * MIB,
        ))
        self.assertFalse(memory_recovered(
            2048 * MIB, -1,
            all_owned_workers_exited=True, tolerance_bytes=8 * MIB,
        ))
        self.assertFalse(memory_drop_observed(2048 * MIB, -1, 32 * MIB))
        self.assertFalse(memory_drop_observed(2048 * MIB, 2048 * MIB, 0))

    def test_observation_allows_small_noise_but_not_large_residual(self):
        self.assertTrue(memory_recovered(
            2048 * MIB, 2040 * MIB,
            all_owned_workers_exited=True, tolerance_bytes=8 * MIB,
        ))
        self.assertFalse(memory_recovered(
            2048 * MIB, 2039 * MIB,
            all_owned_workers_exited=True, tolerance_bytes=8 * MIB,
        ))

    def test_small_or_ambiguous_allocation_delta_is_inconclusive(self):
        self.assertFalse(memory_drop_observed(
            2048 * MIB, 2028 * MIB, 8 * MIB,
        ))
        self.assertFalse(memory_drop_observed(
            2048 * MIB, 2033 * MIB, 32 * MIB,
        ))


if __name__ == "__main__":
    unittest.main()
