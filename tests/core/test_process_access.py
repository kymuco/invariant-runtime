from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from invariant.exec.caps.audit import CapabilityAudit
from invariant.exec.caps.fs import FSAccess
from invariant.api.policies import SandboxLimits
from invariant.exec.caps.ids import CAP_PROCESS
from invariant.exec.caps.policy import CapabilityPolicy, CapabilityPolicyContext
from invariant.exec.caps.process import (
    ProcessAccess,
    ProcessAccessError,
    ProcessIdentityConflict,
    ProcessTimeoutLimitExceeded,
)
from invariant.spec.modes import InstanceMode


class Collector:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(self, name: str, **kwargs: object) -> None:
        self.events.append((name, dict(kwargs)))


def _process(tmp_path: Path, **kwargs) -> ProcessAccess:
    fs = FSAccess(root=tmp_path, write=True, delete=True)
    return ProcessAccess(workspace=fs, **kwargs)


def test_process_run_executes_inside_workspace_relative_cwd(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "payload.txt").write_text("hello\n", encoding="utf-8")

    result = _process(tmp_path).run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; print(Path('payload.txt').read_text().strip())",
        ],
        cwd="nested",
        timeout_s=5.0,
    )

    assert result["ok"] is True
    assert result["returncode"] == 0
    assert result["timed_out"] is False
    assert result["cwd"] == "nested"
    assert result["stdout"].strip() == "hello"
    assert result["stderr"] == ""


def test_process_run_reports_nonzero_exit_without_turning_it_into_capability_failure(
    tmp_path: Path,
) -> None:
    result = _process(tmp_path).run(
        [sys.executable, "-c", "import sys; print('boom', file=sys.stderr); raise SystemExit(7)"],
        timeout_s=5.0,
    )

    assert result["ok"] is False
    assert result["returncode"] == 7
    assert result["timed_out"] is False
    assert "boom" in result["stderr"]


def test_process_run_times_out_with_bounded_outcome(tmp_path: Path) -> None:
    result = _process(tmp_path, max_timeout_s=1.0).run(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        timeout_s=0.05,
    )

    assert result["ok"] is False
    assert result["returncode"] is None
    assert result["timed_out"] is True


def test_process_run_rejects_timeout_above_capability_limit(tmp_path: Path) -> None:
    with pytest.raises(ProcessTimeoutLimitExceeded):
        _process(tmp_path, max_timeout_s=1.0).run(
            [sys.executable, "-c", "print('never')"],
            timeout_s=2.0,
        )


def test_process_run_blocks_workspace_escape(tmp_path: Path) -> None:
    with pytest.raises(ProcessAccessError):
        _process(tmp_path).run(
            [sys.executable, "-c", "print('never')"],
            cwd="../outside",
            timeout_s=1.0,
        )


def test_process_run_blocks_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}_outside"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "outside_link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not supported/allowed on this platform")

    with pytest.raises(ProcessAccessError):
        _process(tmp_path).run(
            [sys.executable, "-c", "print('never')"],
            cwd="outside_link",
            timeout_s=1.0,
        )


def test_process_run_truncates_output_at_capability_limit(tmp_path: Path) -> None:
    result = _process(tmp_path, max_output_chars=10).run(
        [sys.executable, "-c", "print('x' * 30, end='')"],
        timeout_s=5.0,
    )

    assert result["ok"] is True
    assert result["stdout_truncated"] is True
    assert result["stdout"].startswith("x" * 10)
    assert result["stdout"].endswith("...[truncated]")


def test_process_run_audit_omits_command_arguments_and_env_values(tmp_path: Path) -> None:
    collector = Collector()
    process = _process(tmp_path).bind_audit(
        CapabilityAudit(emit=collector.emit),
        cap_id="invariant:capability.process",
    )

    result = process.run(
        [sys.executable, "-c", "print('secret-command-value')"],
        env={"SECRET_TOKEN": "super-secret-value"},
        timeout_s=5.0,
    )

    assert result["ok"] is True
    events = [(name, payload) for name, payload in collector.events if name == "cap.process.run"]
    assert len(events) == 1
    _, event = events[0]
    payload = event["payload"]
    rendered = repr(payload)
    assert "secret-command-value" not in rendered
    assert "super-secret-value" not in rendered
    assert payload["argc"] == 3
    assert payload["cwd"] == "."
    assert payload["env_override_count"] == 1
    assert payload["timed_out"] is False


def test_process_capability_visibility_requires_host_policy_gate() -> None:
    policy = CapabilityPolicy()

    denied = policy.build_view_rules(
        ctx=CapabilityPolicyContext(
            instance_mode=InstanceMode.EPHEMERAL,
            limits=SandboxLimits(allow_process=False),
        )
    )
    allowed = policy.build_view_rules(
        ctx=CapabilityPolicyContext(
            instance_mode=InstanceMode.EPHEMERAL,
            limits=SandboxLimits(allow_process=True),
        )
    )

    assert CAP_PROCESS in denied["deny"]
    assert CAP_PROCESS not in allowed["deny"]



def _wait_terminal(process: ProcessAccess, handle: str, *, timeout_s: float = 5.0) -> dict[str, object]:
    deadline = time.perf_counter() + timeout_s
    current = process.status(handle)
    while current["running"] and time.perf_counter() < deadline:
        time.sleep(0.05)
        current = process.status(handle)
    return current


def test_process_start_replay_reuses_same_durable_handle_without_respawn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    marker = tmp_path / "marker.txt"
    worker = tmp_path / "worker.py"
    worker.write_text(
        (
            "from pathlib import Path\n"
            "import time\n"
            "p = Path('marker.txt')\n"
            "p.write_text((p.read_text() if p.exists() else '') + 'spawned\\n')\n"
            "time.sleep(0.3)\n"
        ),
        encoding="utf-8",
    )
    process = _process(tmp_path)

    first = process.start("operation-replay-001", [sys.executable, "worker.py"])

    def forbidden_popen(*_args, **_kwargs):
        raise AssertionError("replay must not launch another process worker")

    monkeypatch.setattr("invariant.exec.caps.process.subprocess.Popen", forbidden_popen)
    replay = process.start("operation-replay-001", [sys.executable, "worker.py"])

    assert replay["handle"] == first["handle"]
    assert replay["reused"] is True

    terminal = _wait_terminal(process, first["handle"])
    assert terminal["status"] == "exited"
    assert marker.read_text(encoding="utf-8").splitlines() == ["spawned"]


def test_process_start_rejects_same_key_with_different_request(tmp_path: Path) -> None:
    process = _process(tmp_path)
    first = process.start(
        "operation-conflict-001",
        [sys.executable, "-c", "import time; time.sleep(0.2)"],
    )

    with pytest.raises(ProcessIdentityConflict, match="different request"):
        process.start(
            "operation-conflict-001",
            [sys.executable, "-c", "print('different')"],
        )

    terminal = _wait_terminal(process, first["handle"])
    assert terminal["status"] == "exited"


def test_process_status_survives_new_process_access_instance(tmp_path: Path) -> None:
    first_access = _process(tmp_path)
    started = first_access.start(
        "operation-restart-001",
        [sys.executable, "-c", "import time; time.sleep(0.3)"],
    )

    second_access = _process(tmp_path)
    recovered = second_access.status(started["handle"])

    assert recovered["handle"] == started["handle"]
    assert recovered["status"] in {"reserved", "starting", "running", "exited"}

    terminal = _wait_terminal(second_access, started["handle"])
    assert terminal["status"] == "exited"
    assert terminal["returncode"] == 0


def test_process_background_output_is_bounded(tmp_path: Path) -> None:
    process = _process(tmp_path, max_output_chars=10)
    started = process.start(
        "operation-output-001",
        [sys.executable, "-c", "print('x' * 100, end='')"],
    )
    terminal = _wait_terminal(process, started["handle"])
    assert terminal["status"] == "exited"

    output = process.read_output(started["handle"])
    assert output["stdout"] == "x" * 10
    assert output["stdout_truncated"] is True
    assert output["stderr"] == ""


def test_process_background_terminate_is_owned_by_capability(tmp_path: Path) -> None:
    process = _process(tmp_path)
    started = process.start(
        "operation-terminate-001",
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )

    deadline = time.perf_counter() + 5.0
    current = process.status(started["handle"])
    while current["status"] != "running" and time.perf_counter() < deadline:
        time.sleep(0.05)
        current = process.status(started["handle"])

    terminated = process.terminate(started["handle"], kill=True, wait_s=5.0)

    assert terminated["terminated"] is True
    assert terminated["running"] is False
    assert terminated["status"] in {"exited", "unknown"}


def test_process_stale_reserved_state_becomes_unknown_without_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process = _process(tmp_path)
    key = "operation-unknown-001"
    handle = process._handle_for_key(key)
    paths = process._durable_paths(handle)
    process._reserve_state(
        paths["state"],
        {
            "handle": handle,
            "request_digest": "digest",
            "command": [sys.executable, "-c", "print('never')"],
            "cwd": ".",
            "runner_pid": None,
            "child_pid": None,
            "status": "reserved",
            "running": False,
            "returncode": None,
            "reserved_at": "2000-01-01T00:00:00+00:00",
            "started_at": None,
            "finished_at": None,
            "reconciled_at": None,
            "stdout_truncated": False,
            "stderr_truncated": False,
            "error": None,
        },
    )

    def forbidden_popen(*_args, **_kwargs):
        raise AssertionError("status reconciliation must never restart")

    monkeypatch.setattr("invariant.exec.caps.process.subprocess.Popen", forbidden_popen)
    recovered = process.status(handle)

    assert recovered["status"] == "unknown"
    assert recovered["running"] is False
    assert recovered["error"]["type"] == "ProcessOutcomeUnknown"


def test_process_start_audit_omits_key_arguments_and_env_values(tmp_path: Path) -> None:
    collector = Collector()
    process = _process(tmp_path).bind_audit(
        CapabilityAudit(emit=collector.emit),
        cap_id="invariant:capability.process",
    )

    started = process.start(
        "super-secret-operation-key",
        [sys.executable, "-c", "print('secret-command-value')"],
        env={"SECRET_TOKEN": "super-secret-env-value"},
    )
    _wait_terminal(process, started["handle"])

    starts = [
        payload
        for name, payload in collector.events
        if name == "cap.process.start"
    ]
    assert len(starts) == 1
    rendered = repr(starts[0]["payload"])
    assert "super-secret-operation-key" not in rendered
    assert "secret-command-value" not in rendered
    assert "super-secret-env-value" not in rendered
