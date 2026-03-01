from __future__ import annotations

from typing import Any

from invariant.api.policies import EphemeralPolicy, ManagedPolicy
from invariant.ops.host import RuntimeHost
from tests.fake_service import FakePluginService


class _RunApiService(FakePluginService):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run_submit(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("run_submit", dict(kwargs)))
        return {
            "run_id": "af-run-1",
            "state": "submitted",
            "pipeline": str(kwargs.get("pipeline") or ""),
            "idempotent_reuse": False,
        }

    def run_status(self, *, run_id: str) -> dict[str, Any]:
        self.calls.append(("run_status", {"run_id": run_id}))
        return {"run_id": run_id, "state": "running", "pipeline": "safe_patch_apply"}

    def run_report(self, *, run_id: str) -> dict[str, Any]:
        self.calls.append(("run_report", {"run_id": run_id}))
        return {"run_id": run_id, "state": "succeeded", "report_ref": None, "report": {"ok": True}}

    def run_list(self, *, limit: int = 50) -> dict[str, Any]:
        self.calls.append(("run_list", {"limit": int(limit)}))
        return {"items": [{"run_id": "af-run-1", "state": "running"}], "count": 1}

    def run_health(self, *, limit: int = 1000, repair: bool = False) -> dict[str, Any]:
        self.calls.append(("run_health", {"limit": int(limit), "repair": bool(repair)}))
        return {"ok": True, "unknown_state_runs": 0, "terminal_missing_trace_runs": 0}

    def run_observability(
        self,
        *,
        limit: int = 1000,
        fail_spike_window: int = 20,
        fail_spike_threshold_pct: float = 40.0,
        stuck_run_seconds: int = 1800,
        replay_target_pct: float = 95.0,
        trace_target_pct: float = 100.0,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "run_observability",
                {
                    "limit": int(limit),
                    "fail_spike_window": int(fail_spike_window),
                    "fail_spike_threshold_pct": float(fail_spike_threshold_pct),
                    "stuck_run_seconds": int(stuck_run_seconds),
                    "replay_target_pct": float(replay_target_pct),
                    "trace_target_pct": float(trace_target_pct),
                },
            )
        )
        return {
            "ok": True,
            "metrics": {
                "gate_pass_rate_pct": 100.0,
                "rollback_rate_pct": 0.0,
                "replay_pass_rate_pct": 100.0,
                "trace_coverage_pct": 100.0,
            },
            "alerts": [],
        }

    def run_queue_stats(self) -> dict[str, Any]:
        self.calls.append(("run_queue_stats", {}))
        return {"queue_jobs": 1, "counts": {"queued": 1}, "dlq_jobs": 0}

    def run_queue_reclaim(self, *, limit: int = 100) -> dict[str, Any]:
        self.calls.append(("run_queue_reclaim", {"limit": int(limit)}))
        return {"reclaimed": 1, "job_ids": ["af-job-1"]}

    def run_queue_process(
        self,
        *,
        worker_id: str,
        lease_seconds: int | None = None,
        reclaim_expired: bool = True,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "run_queue_process",
                {
                    "worker_id": str(worker_id),
                    "lease_seconds": lease_seconds,
                    "reclaim_expired": bool(reclaim_expired),
                },
            )
        )
        return {"ok": True, "worker_id": worker_id or "worker-auto", "claimed": False}

    def run_dlq(self, *, limit: int = 100) -> dict[str, Any]:
        self.calls.append(("run_dlq", {"limit": int(limit)}))
        return {"items": [], "count": 0}

    def run_migrate_storage(
        self,
        *,
        dry_run: bool = True,
        backup_legacy: bool = True,
        prune_legacy: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "run_migrate_storage",
                {
                    "dry_run": bool(dry_run),
                    "backup_legacy": bool(backup_legacy),
                    "prune_legacy": bool(prune_legacy),
                    "force": bool(force),
                },
            )
        )
        return {
            "ok": True,
            "dry_run": bool(dry_run),
            "changed": True,
            "post": {"queue_items": 1, "dlq_items": 0, "idempotency_items": 1},
        }

    def run_cancel(self, *, run_id: str, reason: str = "cancel_requested") -> dict[str, Any]:
        self.calls.append(("run_cancel", {"run_id": run_id, "reason": reason}))
        return {"run_id": run_id, "state": "aborted", "cancel_reason": reason}

    def run_resume(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("run_resume", dict(kwargs)))
        return {"run_id": str(kwargs["run_id"]), "state": "running", "pipeline": "feature_impl_beta"}

    def run_abort(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("run_abort", dict(kwargs)))
        return {"run_id": str(kwargs["run_id"]), "state": "aborted", "pipeline": "feature_impl_beta"}

    def run_approve(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("run_approve", dict(kwargs)))
        return {"run_id": str(kwargs["run_id"]), "state": "awaiting_approval", "pipeline": "feature_impl_beta"}

    def run_replay(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("run_replay", dict(kwargs)))
        run_id = str(kwargs["run_id"])
        return {
            "run_id": run_id,
            "replay_id": "replay-1",
            "strict": bool(kwargs.get("strict", True)),
            "passed": True,
            "replay": {"passed": True, "failed_checks": []},
            "run": {"run_id": run_id, "state": "succeeded", "pipeline": "safe_patch_apply"},
        }

    def run_replay_batch(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("run_replay_batch", dict(kwargs)))
        return {
            "ok": True,
            "scan_limit": int(kwargs.get("limit", 0)),
            "selected_runs": 2,
            "attempted_runs": 2,
            "passed_runs": 2,
            "failed_runs": 0,
            "results": [
                {"run_id": "af-run-1", "passed": True},
                {"run_id": "af-run-2", "passed": True},
            ],
        }


def _host(service: FakePluginService, *, policy_profile: str | None = None) -> RuntimeHost:
    return RuntimeHost(plugin_service=service, enable_watchdog=False, policy_profile=policy_profile)


def test_run_submit_routes_and_parses_payload():
    service = _RunApiService()
    host = _host(service)
    try:
        out = host.handle(
            {
                "cmd": "run_submit",
                "pipeline": "safe_patch_apply",
                "inputs": {"goal": "lint"},
                "policy": "managed",
                "ttl_seconds": 1800,
                "idempotency_key": "idem-1",
                "fail_fast": True,
                "include_outputs": False,
                "max_steps": 7,
                "workspace_id": "ws-1",
                "defer_execution": True,
                "max_attempts": 4,
                "lease_seconds": 90,
                "step_timeout_seconds": 300,
                "backoff_base_seconds": 2,
            }
        )
        assert out["ok"] is True
        assert out["run"]["run_id"] == "af-run-1"
        method, payload = service.calls[-1]
        assert method == "run_submit"
        assert payload["pipeline"] == "safe_patch_apply"
        assert payload["inputs"] == {"goal": "lint"}
        assert payload["idempotency_key"] == "idem-1"
        assert payload["ttl_seconds"] == 1800
        assert payload["defer_execution"] is True
        assert payload["max_attempts"] == 4
        assert payload["lease_seconds"] == 90
        assert payload["step_timeout_seconds"] == 300
        assert payload["backoff_base_seconds"] == 2
        assert payload["allow_experimental"] is False
        assert isinstance(payload["policy"], ManagedPolicy)
    finally:
        host.close()


def test_run_submit_forwards_host_policy_profile():
    service = _RunApiService()
    host = _host(service, policy_profile="stage")
    try:
        out = host.handle(
            {
                "cmd": "run_submit",
                "pipeline": "safe_patch_apply",
                "inputs": {"goal": "lint stage"},
                "policy": "ephemeral",
            }
        )
        assert out["ok"] is True
        method, payload = service.calls[-1]
        assert method == "run_submit"
        assert payload["policy_profile"] == "stage"
    finally:
        host.close()


def test_run_submit_forwards_experimental_opt_in():
    service = _RunApiService()
    host = _host(service)
    try:
        out = host.handle(
            {
                "cmd": "run_submit",
                "pipeline": "constrained_feature_impl_beta",
                "inputs": {"goal": "experimental"},
                "experimental": True,
            }
        )
        assert out["ok"] is True
        method, payload = service.calls[-1]
        assert method == "run_submit"
        assert payload["allow_experimental"] is True
    finally:
        host.close()


def test_agent_run_other_commands_route_to_service_methods():
    service = _RunApiService()
    host = _host(service)
    try:
        status = host.handle({"cmd": "agent_run_status", "run_id": "af-run-1"})
        report = host.handle({"cmd": "agent_run_report", "run_id": "af-run-1"})
        listed = host.handle({"cmd": "agent_run_list", "limit": 5})
        health = host.handle({"cmd": "agent_run_health", "limit": 20, "repair": True})
        observability = host.handle(
            {
                "cmd": "agent_run_observability",
                "limit": 40,
                "fail_spike_window": 10,
                "fail_spike_threshold_pct": 25.0,
                "stuck_run_seconds": 120,
                "replay_target_pct": 97.0,
                "trace_target_pct": 100.0,
            }
        )
        queue_stats = host.handle({"cmd": "agent_run_queue_stats"})
        queue_reclaim = host.handle({"cmd": "agent_run_queue_reclaim", "limit": 3})
        queue_process = host.handle(
            {"cmd": "agent_run_worker_process", "worker_id": "w-1", "lease_seconds": 45, "reclaim_expired": False}
        )
        dlq = host.handle({"cmd": "agent_run_dlq", "limit": 4})
        migration = host.handle(
            {
                "cmd": "agent_run_migrate_storage",
                "dry_run": False,
                "backup_legacy": True,
                "prune_legacy": True,
                "force": True,
            }
        )
        cancelled = host.handle({"cmd": "agent_run_cancel", "run_id": "af-run-1", "reason": "manual_stop"})
        resumed = host.handle({"cmd": "agent_run_resume", "run_id": "af-run-1", "policy": "ephemeral"})
        aborted = host.handle({"cmd": "agent_run_abort", "run_id": "af-run-1"})
        approved = host.handle(
            {
                "cmd": "agent_run_approve",
                "run_id": "af-run-1",
                "stage": "EDIT",
                "token": "tok-abc",
                "policy": "managed",
            }
        )
        replayed = host.handle(
            {
                "cmd": "agent_run_replay",
                "run_id": "af-run-1",
                "policy": "ephemeral",
                "strict": False,
            }
        )
        replay_batch = host.handle(
            {
                "cmd": "agent_run_replay_batch",
                "limit": 30,
                "max_runs": 7,
                "cooldown_seconds": 120.0,
                "strict": True,
            }
        )

        assert status["ok"] is True and status["run"]["state"] == "running"
        assert report["ok"] is True and report["report"]["report"]["ok"] is True
        assert listed["ok"] is True and listed["count"] == 1
        assert health["ok"] is True and health["health"]["unknown_state_runs"] == 0
        assert observability["ok"] is True
        assert observability["observability"]["metrics"]["replay_pass_rate_pct"] == 100.0
        assert queue_stats["ok"] is True and queue_stats["queue"]["queue_jobs"] == 1
        assert queue_reclaim["ok"] is True and queue_reclaim["reclaimed"] == 1
        assert queue_process["ok"] is True and queue_process["worker"]["claimed"] is False
        assert dlq["ok"] is True and dlq["dlq"]["count"] == 0
        assert migration["ok"] is True and migration["migration"]["changed"] is True
        assert cancelled["ok"] is True and cancelled["run"]["state"] == "aborted"
        assert resumed["ok"] is True and resumed["run"]["state"] == "running"
        assert aborted["ok"] is True and aborted["run"]["state"] == "aborted"
        assert approved["ok"] is True and approved["run"]["state"] == "awaiting_approval"
        assert replayed["ok"] is True and replayed["replay"]["passed"] is True
        assert replay_batch["ok"] is True and replay_batch["batch"]["selected_runs"] == 2

        by_name = {name: payload for name, payload in service.calls}
        assert isinstance(by_name["run_resume"]["policy"], EphemeralPolicy)
        assert isinstance(by_name["run_approve"]["policy"], ManagedPolicy)
        assert isinstance(by_name["run_replay"]["policy"], EphemeralPolicy)
        assert isinstance(by_name["run_replay_batch"]["policy"], EphemeralPolicy)
        assert by_name["run_list"]["limit"] == 5
        assert by_name["run_health"]["limit"] == 20
        assert by_name["run_health"]["repair"] is True
        assert by_name["run_observability"]["limit"] == 40
        assert by_name["run_observability"]["fail_spike_window"] == 10
        assert by_name["run_observability"]["fail_spike_threshold_pct"] == 25.0
        assert by_name["run_observability"]["stuck_run_seconds"] == 120
        assert by_name["run_observability"]["replay_target_pct"] == 97.0
        assert by_name["run_observability"]["trace_target_pct"] == 100.0
        assert by_name["run_queue_reclaim"]["limit"] == 3
        assert by_name["run_queue_process"]["worker_id"] == "w-1"
        assert by_name["run_queue_process"]["lease_seconds"] == 45
        assert by_name["run_queue_process"]["reclaim_expired"] is False
        assert by_name["run_dlq"]["limit"] == 4
        assert by_name["run_migrate_storage"]["dry_run"] is False
        assert by_name["run_migrate_storage"]["backup_legacy"] is True
        assert by_name["run_migrate_storage"]["prune_legacy"] is True
        assert by_name["run_migrate_storage"]["force"] is True
        assert by_name["run_cancel"]["run_id"] == "af-run-1"
        assert by_name["run_cancel"]["reason"] == "manual_stop"
        assert by_name["run_replay"]["strict"] is False
        assert by_name["run_replay_batch"]["limit"] == 30
        assert by_name["run_replay_batch"]["max_runs"] == 7
        assert by_name["run_replay_batch"]["cooldown_seconds"] == 120.0
    finally:
        host.close()


def test_run_submit_rejects_workspace_id_with_workspace_path():
    service = _RunApiService()
    host = _host(service)
    try:
        out = host.handle(
            {
                "cmd": "run_submit",
                "pipeline": "safe_patch_apply",
                "workspace_id": "ws-1",
                "workspace_path": "C:/tmp/workspace",
            }
        )
        assert out["ok"] is False
        assert out["error"] == "invalid_command"
    finally:
        host.close()


def test_run_submit_reports_invalid_policy():
    service = _RunApiService()
    host = _host(service)
    try:
        out = host.handle(
            {
                "cmd": "run_submit",
                "pipeline": "safe_patch_apply",
                "policy": "unknown-policy",
            }
        )
        assert out["ok"] is False
        assert out["error"] == "invalid_command"
    finally:
        host.close()


def test_run_submit_rejects_non_boolean_experimental():
    service = _RunApiService()
    host = _host(service)
    try:
        out = host.handle(
            {
                "cmd": "run_submit",
                "pipeline": "safe_patch_apply",
                "experimental": "yes",
            }
        )
        assert out["ok"] is False
        assert out["error"] == "invalid_command"
    finally:
        host.close()


def test_agent_run_commands_reject_invalid_run_id():
    invalid_ids = [
        "",
        "   ",
        "../../etc/passwd",
        "..\\..\\etc\\passwd",
        "af/run-1",
        "af\\run-1",
        "a" * 129,
    ]
    command_templates = [
        {"cmd": "agent_run_status"},
        {"cmd": "agent_run_report"},
        {"cmd": "agent_run_cancel"},
        {"cmd": "agent_run_resume"},
        {"cmd": "agent_run_abort"},
        {"cmd": "agent_run_approve", "stage": "EDIT", "token": "tok-123"},
        {"cmd": "agent_run_replay"},
    ]

    service = _RunApiService()
    host = _host(service)
    try:
        for template in command_templates:
            for bad_id in invalid_ids:
                cmd = dict(template)
                cmd["run_id"] = bad_id
                out = host.handle(cmd)
                assert out["ok"] is False
                assert out["error"] == "invalid_command"

        called_methods = [name for name, _payload in service.calls]
        assert called_methods == []
    finally:
        host.close()


def test_agent_run_commands_fail_when_service_has_no_run_api():
    host = RuntimeHost(plugin_service=FakePluginService(), enable_watchdog=False)
    try:
        out = host.handle({"cmd": "agent_run_status", "run_id": "af-run-1"})
        assert out["ok"] is False
        assert out["error"] == "invalid_command"
    finally:
        host.close()
