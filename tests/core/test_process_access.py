from __future__ import annotations

import sys
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
