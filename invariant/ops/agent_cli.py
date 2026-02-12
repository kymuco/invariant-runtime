from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from invariant.exec.caps.fs import FSAccess
from manifests.approval_gate_plugin.approval_gate_plugin import ApprovalGatePlugin
from manifests.coding_agent_plugin.coding_agent_plugin import CodingAgentPlugin
from manifests.planner_reviewer_plugin.planner_reviewer_plugin import PlannerReviewerPlugin
from manifests.quality_gate_plugin.quality_gate_plugin import QualityGatePlugin
from manifests.repo_tools_plugin.repo_tools_plugin import RepoToolsPlugin
from manifests.schema_guard_plugin.schema_guard_plugin import SchemaGuardPlugin
from manifests.trace_sink_plugin.trace_sink_plugin import TraceSinkPlugin


def _meta(pid: str):
    return SimpleNamespace(id=pid, version="0.1.0")


class _RuntimeStack:
    def __init__(self, *, workspace: Path, run_registry_root: Path):
        self.workspace = workspace
        self.run_registry_root = run_registry_root
        self.schema_guard: SchemaGuardPlugin | None = None
        self.repo_tools: RepoToolsPlugin | None = None
        self.quality_gate: QualityGatePlugin | None = None
        self.approval_gate: ApprovalGatePlugin | None = None
        self.trace_sink: TraceSinkPlugin | None = None
        self.planner: PlannerReviewerPlugin | None = None
        self.agent: CodingAgentPlugin | None = None

    def start(self) -> None:
        self.schema_guard = SchemaGuardPlugin(meta=_meta("invariant:schema_guard"))
        self.schema_guard.initialize()

        self.repo_tools = RepoToolsPlugin(meta=_meta("invariant:repo_tools"))
        self.repo_tools.initialize(
            fs_sandbox=FSAccess(root=self.workspace, write=True, delete=True),
            workspace=None,
            schema_guard=self.schema_guard,
        )

        self.quality_gate = QualityGatePlugin(meta=_meta("invariant:quality_gate"))
        self.quality_gate.initialize(
            repo_tools=self.repo_tools,
            schema_guard=self.schema_guard,
            allowed_commands=["python", "pytest", "ruff", "mypy", "git"],
        )

        self.approval_gate = ApprovalGatePlugin(meta=_meta("invariant:approval_gate"))
        self.approval_gate.initialize(
            storage_path=self.run_registry_root / "approval_gate.json",
        )

        self.trace_sink = TraceSinkPlugin(meta=_meta("invariant:trace_sink"))
        self.trace_sink.initialize()

        self.planner = PlannerReviewerPlugin(meta=_meta("invariant:planner_reviewer"))
        self.planner.initialize(schema_guard=self.schema_guard, strict_review=True)

        self.agent = CodingAgentPlugin(meta=_meta("invariant:coding_agent"))
        self.agent.initialize(
            repo_tools=self.repo_tools,
            quality_gate=self.quality_gate,
            approval_gate=self.approval_gate,
            trace_sink=self.trace_sink,
            schema_guard=self.schema_guard,
            run_registry_dir=self.run_registry_root / "coding_agent",
        )

    def stop(self) -> None:
        if self.agent is not None:
            self.agent.shutdown()
        if self.planner is not None:
            self.planner.shutdown()
        if self.trace_sink is not None:
            self.trace_sink.shutdown()
        if self.approval_gate is not None:
            self.approval_gate.shutdown()
        if self.quality_gate is not None:
            self.quality_gate.shutdown()
        if self.repo_tools is not None:
            self.repo_tools.shutdown()
        if self.schema_guard is not None:
            self.schema_guard.shutdown()


def _parse_run_gate(value: str | None) -> bool | None:
    if value is None:
        return None
    raw = str(value).strip().lower()
    if raw in {"auto", ""}:
        return None
    if raw in {"true", "1", "yes", "y"}:
        return True
    if raw in {"false", "0", "no", "n"}:
        return False
    raise ValueError("run_gate must be one of: auto, true, false")


def _load_task(*, task_file: str | None, task_json: str | None) -> dict[str, Any]:
    if bool(task_file) == bool(task_json):
        raise ValueError("Provide exactly one of --task-file or --task-json")

    if task_file:
        path = Path(task_file)
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = json.loads(str(task_json))

    if not isinstance(data, dict):
        raise ValueError("task payload must be JSON object")
    return dict(data)


def _json_out(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _runtime_from_args(args: argparse.Namespace) -> _RuntimeStack:
    workspace = Path(args.workspace).resolve()
    run_registry_root = Path(args.run_registry_dir).resolve()
    run_registry_root.mkdir(parents=True, exist_ok=True)
    stack = _RuntimeStack(workspace=workspace, run_registry_root=run_registry_root)
    stack.start()
    return stack


def _cmd_plan(args: argparse.Namespace) -> int:
    task = _load_task(task_file=args.task_file, task_json=args.task_json)
    run_gate = _parse_run_gate(args.run_gate)
    stack = _runtime_from_args(args)
    try:
        planner = stack.planner
        if planner is None:
            raise RuntimeError("planner is not available")
        out = planner.prepare_task(
            task,
            run_gate=run_gate,
            review=(not args.no_review),
            fail_on_review_error=(not args.allow_review_warnings),
        )
        _json_out(out)
        return 0
    finally:
        stack.stop()


def _cmd_run(args: argparse.Namespace) -> int:
    task = _load_task(task_file=args.task_file, task_json=args.task_json)
    run_gate = _parse_run_gate(args.run_gate)
    stack = _runtime_from_args(args)
    try:
        agent = stack.agent
        if agent is None:
            raise RuntimeError("coding_agent is not available")
        report = agent.run(
            task,
            profile=str(args.profile),
            dry_run=bool(args.dry_run),
            run_gate=run_gate,
        )
        _json_out(report)
        status = str(report.get("status") or "failed")
        return 0 if status in {"success", "partial"} else 1
    finally:
        stack.stop()


def _cmd_status(args: argparse.Namespace) -> int:
    stack = _runtime_from_args(args)
    try:
        agent = stack.agent
        if agent is None:
            raise RuntimeError("coding_agent is not available")
        out = agent.status(str(args.run_id))
        _json_out(out)
        return 0
    finally:
        stack.stop()


def _cmd_approve(args: argparse.Namespace) -> int:
    stack = _runtime_from_args(args)
    try:
        gate = stack.approval_gate
        if gate is None:
            raise RuntimeError("approval_gate is not available")
        out = gate.approve(str(args.run_id), str(args.stage), str(args.token))
        _json_out(out)
        return 0 if bool(out.get("ok", False)) else 1
    finally:
        stack.stop()


def _cmd_abort(args: argparse.Namespace) -> int:
    stack = _runtime_from_args(args)
    try:
        agent = stack.agent
        if agent is None:
            raise RuntimeError("coding_agent is not available")
        out = agent.abort(str(args.run_id))
        _json_out(out)
        return 0 if bool(out.get("acknowledged", False)) else 1
    finally:
        stack.stop()


def _cmd_suite(args: argparse.Namespace) -> int:
    from invariant.ops.beta_readiness import run_beta_readiness_suite_from_dir

    report = run_beta_readiness_suite_from_dir(
        suite_dir=args.suite_dir,
        include_extended=(not args.core_only),
        strict_core_lock=(not args.no_strict_lock),
        workspace=args.workspace,
        run_registry_dir=args.run_registry_dir,
        output_root=args.output_root,
        max_cases=args.max_cases,
    )
    _json_out(report)
    gates = report.get("release_gates")
    if not isinstance(gates, dict):
        return 1
    return 0 if all(bool(v) for v in gates.values()) else 1


def _cmd_suite_lock(args: argparse.Namespace) -> int:
    from invariant.ops.beta_readiness import write_core_suite_lock

    payload = write_core_suite_lock(args.suite_dir)
    _json_out(payload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="invariant-agent")
    p.add_argument("--workspace", default=".", help="Workspace root for repo operations")
    p.add_argument(
        "--run-registry-dir",
        default=str(Path(".invariant") / "run_registry"),
        help="Shared registry directory for run/approval persistence",
    )

    sp = p.add_subparsers(dest="cmd", required=True)

    p_plan = sp.add_parser("plan", help="Build and review task plan")
    p_plan.add_argument("--task-file", default=None, help="Path to task JSON file")
    p_plan.add_argument("--task-json", default=None, help="Inline task JSON object")
    p_plan.add_argument("--run-gate", default="auto", help="auto|true|false")
    p_plan.add_argument("--no-review", action="store_true", help="Skip planner review")
    p_plan.add_argument(
        "--allow-review-warnings",
        action="store_true",
        help="Do not fail on review findings",
    )
    p_plan.set_defaults(func=_cmd_plan)

    p_run = sp.add_parser("run", help="Run coding agent task")
    p_run.add_argument("--task-file", default=None, help="Path to task JSON file")
    p_run.add_argument("--task-json", default=None, help="Inline task JSON object")
    p_run.add_argument(
        "--profile",
        default="safe_fix",
        choices=["analyze_only", "safe_fix", "feature_impl_beta"],
        help="Execution profile",
    )
    p_run.add_argument("--dry-run", action="store_true", help="Run without writing changes")
    p_run.add_argument("--run-gate", default="auto", help="auto|true|false")
    p_run.set_defaults(func=_cmd_run)

    p_status = sp.add_parser("status", help="Read run status by run_id")
    p_status.add_argument("run_id")
    p_status.set_defaults(func=_cmd_status)

    p_approve = sp.add_parser("approve", help="Approve stage token")
    p_approve.add_argument("run_id")
    p_approve.add_argument("stage")
    p_approve.add_argument("token")
    p_approve.set_defaults(func=_cmd_approve)

    p_abort = sp.add_parser("abort", help="Request abort by run_id")
    p_abort.add_argument("run_id")
    p_abort.set_defaults(func=_cmd_abort)

    p_suite = sp.add_parser("suite", help="Run P0-010 beta readiness suite")
    p_suite.add_argument("--suite-dir", default="beta_suite", help="Suite root directory")
    p_suite.add_argument("--core-only", action="store_true", help="Run only core suite cases")
    p_suite.add_argument("--no-strict-lock", action="store_true", help="Do not fail on core lock mismatch")
    p_suite.add_argument("--output-root", default=str(Path(".invariant") / "beta_runs"))
    p_suite.add_argument("--max-cases", type=int, default=None)
    p_suite.set_defaults(func=_cmd_suite)

    p_suite_lock = sp.add_parser("suite-lock", help="Regenerate core suite lock file")
    p_suite_lock.add_argument("--suite-dir", default="beta_suite", help="Suite root directory")
    p_suite_lock.set_defaults(func=_cmd_suite_lock)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        fn = args.func
        return int(fn(args))
    except Exception as e:  # noqa: BLE001
        _json_out({"ok": False, "error": e.__class__.__name__, "message": str(e)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
