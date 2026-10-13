"""Gate B.3: CPU-only fail-closed evidence policy checks.

These deliberately DO NOT launch CUDA or allocate GPU memory. Native GPU
proof is a separate explicit script that fails if CUDA evidence is missing.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from gate_b_cuda_windows_smoke import (
    MIB,
    failure_chain,
    validate_cuda_ready,
    cuda_worker_interpreter,
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


    def test_worker_failure_chain_does_not_hide_cuda_reason(self):
        try:
            try:
                raise RuntimeError("CUDA worker not READY: cudaError=out-of-memory")
            except RuntimeError as worker:
                raise RuntimeError("candidate startup failed; restored=False") from worker
        except RuntimeError as outer:
            diagnostic = failure_chain(outer)
        self.assertIn("candidate startup failed; restored=False", diagnostic)
        self.assertIn("CUDA worker not READY", diagnostic)
        self.assertIn("out-of-memory", diagnostic)

    def test_worker_failure_chain_survives_hidden_context(self):
        try:
            try:
                raise ValueError("child bootstrap failed")
            except ValueError:
                raise RuntimeError("worker stopped") from None
        except RuntimeError as outer:
            diagnostic = failure_chain(outer)
        self.assertEqual(diagnostic, "RuntimeError: worker stopped")


    def test_ready_message_and_alive_process_pass(self):
        msg = {"kind": "ready", "pid": 123, "revision": "v1", "mib": 32}
        self.assertIsNone(validate_cuda_ready(
            msg, expected_pid=123, revision="v1", mib=32, exit_code=None
        ))

    def test_ready_message_from_already_exited_worker_is_rejected(self):
        msg = {"kind": "ready", "pid": 123, "revision": "v1", "mib": 32}
        with self.assertRaisesRegex(RuntimeError, "exited after READY; returncode=0"):
            validate_cuda_ready(
                msg, expected_pid=123, revision="v1", mib=32, exit_code=0
            )

    def test_ready_mismatch_shows_each_specific_field(self):
        msg = {"kind": "ready", "pid": 321, "revision": "bad", "mib": 16}
        with self.assertRaises(RuntimeError) as error:
            validate_cuda_ready(
                msg, expected_pid=123, revision="v1", mib=32, exit_code=None
            )
        diagnostic = str(error.exception)
        self.assertIn("pid: expected=123, observed=321", diagnostic)
        self.assertIn("revision: expected='v1', observed='bad'", diagnostic)
        self.assertIn("mib: expected=32, observed=16", diagnostic)


    def test_windows_venv_selects_real_python_with_venv_identity(self):
        import sys

        with patch.object(sys, "platform", "win32"):
            with patch.object(sys, "executable", r"C:\venv\Scripts\python.exe"):
                with patch.object(
                    sys, "_base_executable", r"C:\Python\python.exe", create=True
                ):
                    binary, env = cuda_worker_interpreter()
        self.assertEqual(binary, r"C:\Python\python.exe")
        self.assertIsNotNone(env)
        self.assertEqual(
            env["__PYVENV_LAUNCHER__"], r"C:\venv\Scripts\python.exe"
        )

    def test_regular_python_does_not_change_subprocess_environment(self):
        import sys

        with patch.object(sys, "platform", "win32"):
            with patch.object(sys, "executable", r"C:\Python\python.exe"):
                with patch.object(sys, "_base_executable", r"C:\Python\python.exe", create=True):
                    binary, env = cuda_worker_interpreter()
        self.assertEqual(binary, r"C:\Python\python.exe")
        self.assertIsNone(env)


if __name__ == "__main__":
    unittest.main()
