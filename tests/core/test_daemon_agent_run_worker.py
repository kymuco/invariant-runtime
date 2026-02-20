from __future__ import annotations

import json
import threading
import time

from invariant.ops.daemon import (
    _agent_run_alert_loop,
    _agent_run_queue_worker_loop,
    _agent_run_replay_sampler_loop,
)


class _FakeRuntimeHost:
    def __init__(self, fn):
        self._fn = fn
        self.calls: list[dict[str, object]] = []

    def handle(self, command: dict[str, object]) -> dict[str, object]:
        payload = dict(command)
        self.calls.append(payload)
        return dict(self._fn(payload, len(self.calls)))


def test_agent_run_queue_worker_loop_exits_on_invalid_command():
    stop_event = threading.Event()
    host = _FakeRuntimeHost(
        lambda _cmd, _n: {
            "ok": False,
            "error": "invalid_command",
            "message": "agent run API is not available in this service",
        }
    )

    _agent_run_queue_worker_loop(
        runtime_host=host,  # type: ignore[arg-type]
        stop_event=stop_event,
        worker_id="w-invalid",
        lease_seconds=30,
        poll_interval_s=0.01,
        reclaim_expired=True,
    )

    assert len(host.calls) == 1
    first = host.calls[0]
    assert first["cmd"] == "agent_run_worker_process"
    assert first["worker_id"] == "w-invalid"
    assert first["lease_seconds"] == 30
    assert first["reclaim_expired"] is True


def test_agent_run_queue_worker_loop_processes_until_stop_event():
    stop_event = threading.Event()

    def _handler(_cmd: dict[str, object], n: int) -> dict[str, object]:
        if n >= 3:
            stop_event.set()
        return {
            "ok": True,
            "worker": {
                "ok": True,
                "worker_id": "w-queue",
                "claimed": n < 3,
            },
        }

    host = _FakeRuntimeHost(_handler)

    _agent_run_queue_worker_loop(
        runtime_host=host,  # type: ignore[arg-type]
        stop_event=stop_event,
        worker_id="w-queue",
        lease_seconds=45,
        poll_interval_s=0.01,
        reclaim_expired=False,
    )

    assert len(host.calls) == 3
    for item in host.calls:
        assert item["cmd"] == "agent_run_worker_process"
        assert item["worker_id"] == "w-queue"
        assert item["lease_seconds"] == 45
        assert item["reclaim_expired"] is False


def test_agent_run_alert_loop_exits_on_invalid_command(tmp_path):
    stop_event = threading.Event()
    host = _FakeRuntimeHost(
        lambda _cmd, _n: {
            "ok": False,
            "error": "invalid_command",
            "message": "agent run API is not available in this service",
        }
    )
    sent: list[dict[str, object]] = []

    def _notify(**kwargs):
        sent.append(dict(kwargs))
        return {"ok": True, "status_code": 200}

    _agent_run_alert_loop(
        runtime_host=host,  # type: ignore[arg-type]
        stop_event=stop_event,
        alert_config={
            "profile": "dev",
            "webhook_url": "https://alerts.local/hook",
            "timeout_s": 1.0,
            "cooldown_s": 60,
            "poll_interval_s": 0.01,
            "scan_limit": 20,
            "fail_spike_window": 10,
            "fail_spike_threshold_pct": 40.0,
            "stuck_run_seconds": 120,
            "replay_target_pct": 95.0,
            "trace_target_pct": 100.0,
            "state_path": str(tmp_path / "alerts_state.json"),
        },
        notifier=_notify,
        max_cycles=5,
    )

    assert len(host.calls) == 1
    assert host.calls[0]["cmd"] == "agent_run_observability"
    assert sent == []


def test_agent_run_alert_loop_sends_and_suppresses_by_cooldown(tmp_path):
    stop_event = threading.Event()
    ts = float(time.time())

    def _handler(_cmd: dict[str, object], n: int) -> dict[str, object]:
        if n == 1:
            return {
                "ok": True,
                "observability": {
                    "ok": False,
                    "generated_at_unix": ts,
                    "metrics": {"replay_pass_rate_pct": 50.0},
                    "alerts": [
                        {
                            "id": "replay_pass_below_target",
                            "triggered": True,
                            "severity": "critical",
                            "value_pct": 50.0,
                            "threshold_pct": 95.0,
                        }
                    ],
                },
            }
        if n == 2:
            return {
                "ok": True,
                "observability": {
                    "ok": False,
                    "generated_at_unix": ts + 1.0,
                    "metrics": {"replay_pass_rate_pct": 50.0},
                    "alerts": [
                        {
                            "id": "replay_pass_below_target",
                            "triggered": True,
                            "severity": "critical",
                            "value_pct": 50.0,
                            "threshold_pct": 95.0,
                        }
                    ],
                },
            }
        return {
            "ok": True,
            "observability": {
                "ok": True,
                "generated_at_unix": ts + 2.0,
                "metrics": {"replay_pass_rate_pct": 100.0},
                "alerts": [],
            },
        }

    host = _FakeRuntimeHost(_handler)
    delivered: list[dict[str, object]] = []

    def _notify(**kwargs):
        delivered.append(dict(kwargs))
        return {"ok": True, "status_code": 204}

    state_path = tmp_path / "alerts_state.json"
    _agent_run_alert_loop(
        runtime_host=host,  # type: ignore[arg-type]
        stop_event=stop_event,
        alert_config={
            "profile": "stage",
            "webhook_url": "https://alerts.local/hook",
            "timeout_s": 1.0,
            "cooldown_s": 3600,
            "poll_interval_s": 0.01,
            "scan_limit": 20,
            "fail_spike_window": 10,
            "fail_spike_threshold_pct": 40.0,
            "stuck_run_seconds": 120,
            "replay_target_pct": 95.0,
            "trace_target_pct": 100.0,
            "state_path": str(state_path),
        },
        notifier=_notify,
        max_cycles=3,
    )

    assert len(host.calls) == 3
    assert len(delivered) == 1
    first_payload = delivered[0].get("payload")
    assert isinstance(first_payload, dict)
    assert first_payload.get("alert_id") == "replay_pass_below_target"

    state_obj = json.loads(state_path.read_text(encoding="utf-8"))
    assert state_obj["alerts"] == {}


def test_agent_run_replay_sampler_loop_exits_on_invalid_command():
    stop_event = threading.Event()
    host = _FakeRuntimeHost(
        lambda _cmd, _n: {
            "ok": False,
            "error": "invalid_command",
            "message": "agent run API is not available in this service",
        }
    )
    _agent_run_replay_sampler_loop(
        runtime_host=host,  # type: ignore[arg-type]
        stop_event=stop_event,
        sampler_config={
            "enabled": True,
            "profile": "dev",
            "poll_interval_s": 0.01,
            "scan_limit": 30,
            "max_runs": 5,
            "cooldown_seconds": 60.0,
            "strict": True,
            "policy": "ephemeral",
            "ttl_seconds": 1200,
        },
        max_cycles=5,
    )
    assert len(host.calls) == 1
    first = host.calls[0]
    assert first["cmd"] == "agent_run_replay_batch"
    assert first["limit"] == 30
    assert first["max_runs"] == 5
    assert first["cooldown_seconds"] == 60.0


def test_agent_run_replay_sampler_loop_processes_until_stop_event():
    stop_event = threading.Event()

    def _handler(_cmd: dict[str, object], n: int) -> dict[str, object]:
        if n >= 3:
            stop_event.set()
        return {
            "ok": True,
            "batch": {
                "ok": True,
                "selected_runs": 1 if n < 3 else 0,
                "attempted_runs": 1 if n < 3 else 0,
                "passed_runs": 1 if n < 3 else 0,
                "failed_runs": 0,
            },
        }

    host = _FakeRuntimeHost(_handler)
    _agent_run_replay_sampler_loop(
        runtime_host=host,  # type: ignore[arg-type]
        stop_event=stop_event,
        sampler_config={
            "enabled": True,
            "profile": "stage",
            "poll_interval_s": 0.01,
            "scan_limit": 20,
            "max_runs": 4,
            "cooldown_seconds": 120.0,
            "strict": False,
            "policy": "managed",
            "ttl_seconds": 600,
        },
    )
    assert len(host.calls) == 3
    for item in host.calls:
        assert item["cmd"] == "agent_run_replay_batch"
        assert item["limit"] == 20
        assert item["max_runs"] == 4
        assert item["policy"] == "managed"
        assert item["strict"] is False
