from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path, PurePosixPath
from typing import Any

from invariant.ops.agent_cli import _RuntimeStack

_LOCK_VERSION = 1
_DEFAULT_OUTPUT_ROOT = Path(".invariant") / "beta_runs"


def run_beta_readiness_suite_from_dir(
    *,
    suite_dir: str | Path = "beta_suite",
    include_extended: bool = True,
    strict_core_lock: bool = True,
    workspace: str | Path = ".",
    run_registry_dir: str | Path = Path(".invariant") / "run_registry",
    output_root: str | Path | None = _DEFAULT_OUTPUT_ROOT,
    max_cases: int | None = None,
) -> dict[str, Any]:
    suite_root = Path(suite_dir).resolve()
    lock_info = verify_core_suite_lock(suite_root, strict=strict_core_lock)
    cases = load_beta_suite_cases(
        suite_root,
        include_extended=include_extended,
        max_cases=max_cases,
    )
    report = run_beta_readiness_suite(
        cases,
        workspace=workspace,
        run_registry_dir=run_registry_dir,
    )
    report["suite"] = {
        "suite_dir": str(suite_root),
        "include_extended": bool(include_extended),
        "strict_core_lock": bool(strict_core_lock),
        "cases_loaded": len(cases),
        "core_lock": lock_info,
    }

    if output_root is not None:
        paths = persist_beta_report(report, output_root=output_root)
        report["artifacts"] = paths

    return report


def run_beta_readiness_suite(
    tasks: list[dict[str, Any]],
    *,
    workspace: str | Path = ".",
    run_registry_dir: str | Path = Path(".invariant") / "run_registry",
) -> dict[str, Any]:
    root = Path(workspace).resolve()
    registry_root = Path(run_registry_dir).resolve()
    registry_root.mkdir(parents=True, exist_ok=True)

    stack = _RuntimeStack(workspace=root, run_registry_root=registry_root)
    stack.start()
    try:
        agent = stack.agent
        if agent is None:
            raise RuntimeError("coding_agent is not available")

        started = time.time()
        outcomes: list[dict[str, Any]] = []
        for idx, item in enumerate(tasks):
            if not isinstance(item, dict):
                outcomes.append(
                    {
                        "task_index": idx,
                        "status": "invalid",
                        "error": "task item must be object",
                        "expectation_ok": False,
                    }
                )
                continue

            case_id = str(item.get("id") or f"suite_task_{idx + 1}")
            profile = str(item.get("profile") or "safe_fix")
            dry_run = bool(item.get("dry_run", False))
            run_gate_raw = item.get("run_gate")
            run_gate = None if run_gate_raw is None else bool(run_gate_raw)
            rollback_observed = bool(item.get("rollback_observed", False))
            src = str(item.get("_source") or "")
            setup_err = _prepare_case_workspace(root, item)
            if setup_err is not None:
                outcomes.append(
                    {
                        "task_id": case_id,
                        "profile": profile,
                        "source": src,
                        "status": "invalid",
                        "error": setup_err,
                        "expectation_ok": False,
                    }
                )
                continue

            task_payload_raw = item.get("task")
            if not isinstance(task_payload_raw, dict):
                outcomes.append(
                    {
                        "task_id": case_id,
                        "profile": profile,
                        "source": src,
                        "status": "invalid",
                        "error": "task payload must be object",
                        "expectation_ok": False,
                    }
                )
                continue
            task_payload = dict(task_payload_raw)
            task_payload.setdefault("task_id", case_id)

            t0 = time.time()
            report = agent.run(task_payload, profile=profile, dry_run=dry_run, run_gate=run_gate)
            t1 = time.time()

            summary = report.get("summary")
            summary_obj = dict(summary) if isinstance(summary, dict) else {}
            status = str(report.get("status") or "failed")
            expectation = evaluate_case_expectation(item, report)
            outcome = {
                "task_id": case_id,
                "profile": profile,
                "source": src,
                "run_id": str(report.get("run_id") or ""),
                "status": status,
                "duration_ms": int((t1 - t0) * 1000),
                "reason_code": summary_obj.get("reason_code"),
                "has_trace_ref": isinstance(summary_obj.get("trace_ref"), str),
                "has_registry_ref": isinstance(summary_obj.get("registry_ref"), str),
                "has_gate_report": isinstance(report.get("gate"), dict),
                "rollback_observed": rollback_observed,
                "expectation_defined": bool(expectation.get("checked", False)),
                "expectation_ok": bool(expectation.get("ok", False)),
                "expectation_errors": list(expectation.get("errors", [])),
            }
            outcomes.append(outcome)

        finished = time.time()
        metrics = _compute_metrics(outcomes)
        gates = _evaluate_release_gates(metrics)
        blockers = _collect_blockers(outcomes, gates)
        return {
            "started_at_unix": started,
            "finished_at_unix": finished,
            "duration_ms": int((finished - started) * 1000),
            "tasks_total": len(tasks),
            "outcomes": outcomes,
            "metrics": metrics,
            "release_gates": gates,
            "blockers": blockers,
        }
    finally:
        stack.stop()


def load_beta_suite_cases(
    suite_dir: str | Path,
    *,
    include_extended: bool = True,
    max_cases: int | None = None,
) -> list[dict[str, Any]]:
    root = Path(suite_dir).resolve()
    if not root.exists():
        raise FileNotFoundError(f"suite_dir not found: {root}")

    cases: list[dict[str, Any]] = []
    groups = ["core", "extended"] if include_extended else ["core"]
    for group in groups:
        group_dir = root / group
        if not group_dir.exists():
            continue
        for path in sorted(group_dir.glob("*.json")):
            with path.open("r", encoding="utf-8-sig") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise ValueError(f"suite case is not object: {path}")
            item = dict(raw)
            case_id = item.get("id")
            if not isinstance(case_id, str) or not case_id.strip():
                raise ValueError(f"suite case id is required: {path}")
            item.setdefault("group", group)
            item["_source"] = str(path.relative_to(root).as_posix())
            cases.append(item)

    if max_cases is not None:
        lim = max(0, int(max_cases))
        return cases[:lim]
    return cases


def compute_core_suite_lock_payload(suite_dir: str | Path) -> dict[str, Any]:
    root = Path(suite_dir).resolve()
    core_dir = root / "core"
    if not core_dir.exists():
        raise FileNotFoundError(f"core suite directory not found: {core_dir}")
    hashes: dict[str, str] = {}
    for path in sorted(core_dir.glob("*.json")):
        rel = str(path.relative_to(root).as_posix())
        hashes[rel] = _file_sha256(path)

    digest = _digest_hash_map(hashes)
    return {
        "version": _LOCK_VERSION,
        "core": {
            "count": len(hashes),
            "sha256": digest,
            "files": hashes,
        },
    }


def write_core_suite_lock(suite_dir: str | Path) -> dict[str, Any]:
    root = Path(suite_dir).resolve()
    payload = compute_core_suite_lock_payload(root)
    lock_path = root / "suite.lock.json"
    with lock_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
    return payload


def verify_core_suite_lock(suite_dir: str | Path, *, strict: bool = True) -> dict[str, Any]:
    root = Path(suite_dir).resolve()
    lock_path = root / "suite.lock.json"
    actual = compute_core_suite_lock_payload(root)

    if not lock_path.exists():
        msg = f"suite lock file is missing: {lock_path}"
        if strict:
            raise RuntimeError(msg)
        return {
            "ok": False,
            "error": msg,
            "actual": actual,
            "expected": None,
            "mismatches": ["lock_missing"],
        }

    with lock_path.open("r", encoding="utf-8-sig") as f:
        expected = json.load(f)
    if not isinstance(expected, dict):
        raise ValueError(f"suite lock must be object: {lock_path}")

    mismatches: list[str] = []
    if int(expected.get("version", -1)) != int(actual["version"]):
        mismatches.append("version")
    exp_core = expected.get("core")
    if not isinstance(exp_core, dict):
        mismatches.append("core_missing")
        exp_core = {}
    if int(exp_core.get("count", -1)) != int(actual["core"]["count"]):
        mismatches.append("core.count")
    if str(exp_core.get("sha256", "")) != str(actual["core"]["sha256"]):
        mismatches.append("core.sha256")

    exp_files = exp_core.get("files")
    if not isinstance(exp_files, dict):
        exp_files = {}
    act_files = actual["core"]["files"]
    if sorted(exp_files.keys()) != sorted(act_files.keys()):
        mismatches.append("core.files.keys")
    else:
        for key in sorted(act_files.keys()):
            if str(exp_files.get(key)) != str(act_files.get(key)):
                mismatches.append(f"core.files.{key}")
                break

    ok = len(mismatches) == 0
    if strict and (not ok):
        details = ", ".join(mismatches)
        raise RuntimeError(
            f"core suite lock mismatch: {details}. "
            "If changes are intentional, regenerate suite.lock.json via write_core_suite_lock()."
        )
    return {
        "ok": ok,
        "expected": expected,
        "actual": actual,
        "mismatches": mismatches,
    }


def evaluate_case_expectation(case: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    exp_raw = case.get("expect")
    if not isinstance(exp_raw, dict):
        return {"checked": False, "ok": True, "errors": []}

    errors: list[str] = []
    status = str(report.get("status") or "")
    summary = report.get("summary")
    summary_obj = dict(summary) if isinstance(summary, dict) else {}
    reason_code = summary_obj.get("reason_code")

    exp_status = exp_raw.get("status_in")
    if isinstance(exp_status, list) and exp_status:
        allowed = [str(x) for x in exp_status]
        if status not in allowed:
            errors.append(f"status expected in {allowed}, got {status!r}")

    exp_reason = exp_raw.get("reason_code_in")
    if isinstance(exp_reason, list) and exp_reason:
        allowed_reason = [None if x is None else str(x) for x in exp_reason]
        actual_reason = None if reason_code is None else str(reason_code)
        if actual_reason not in allowed_reason:
            errors.append(f"reason_code expected in {allowed_reason}, got {actual_reason!r}")

    exp_trace = exp_raw.get("has_trace_ref")
    if isinstance(exp_trace, bool):
        has_trace = isinstance(summary_obj.get("trace_ref"), str)
        if has_trace != exp_trace:
            errors.append(f"has_trace_ref expected {exp_trace}, got {has_trace}")

    exp_registry = exp_raw.get("has_registry_ref")
    if isinstance(exp_registry, bool):
        has_registry = isinstance(summary_obj.get("registry_ref"), str)
        if has_registry != exp_registry:
            errors.append(f"has_registry_ref expected {exp_registry}, got {has_registry}")

    return {"checked": True, "ok": len(errors) == 0, "errors": errors}


def persist_beta_report(
    report: dict[str, Any],
    *,
    output_root: str | Path = _DEFAULT_OUTPUT_ROOT,
) -> dict[str, str]:
    root = Path(output_root).resolve()
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    run_dir = root / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    report_path = run_dir / "report.json"
    summary_path = run_dir / "summary.md"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
    summary_path.write_text(_build_summary_markdown(report), encoding="utf-8")
    return {"run_dir": str(run_dir), "report_json": str(report_path), "summary_md": str(summary_path)}


def _build_summary_markdown(report: dict[str, Any]) -> str:
    metrics = report.get("metrics")
    gates = report.get("release_gates")
    blockers = report.get("blockers")
    m = dict(metrics) if isinstance(metrics, dict) else {}
    g = dict(gates) if isinstance(gates, dict) else {}
    b = blockers if isinstance(blockers, list) else []

    lines: list[str] = []
    lines.append("# Beta Readiness Report")
    lines.append("")
    lines.append(f"- tasks_total: {report.get('tasks_total')}")
    lines.append(f"- runs_total: {m.get('runs_total')}")
    lines.append(f"- safe_fix_success_rate: {m.get('safe_fix_success_rate')}")
    lines.append(f"- rollback_rate: {m.get('rollback_rate')}")
    lines.append(f"- full_trace_rate: {m.get('full_trace_rate')}")
    lines.append(f"- expectations_pass_rate: {m.get('expectations_pass_rate')}")
    lines.append("")
    lines.append("## Release Gates")
    lines.append("")
    for key in sorted(g.keys()):
        lines.append(f"- {key}: {g[key]}")
    lines.append("")
    lines.append("## Blockers")
    lines.append("")
    if not b:
        lines.append("- none")
    else:
        for item in b[:50]:
            lines.append(f"- {json.dumps(item, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


def _compute_metrics(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    runs = [o for o in outcomes if str(o.get("status")) in {"success", "partial", "failed", "aborted"}]
    safe_fix_runs = [o for o in runs if str(o.get("profile")) == "safe_fix"]
    safe_fix_success = [o for o in safe_fix_runs if str(o.get("status")) == "success"]

    applied_runs = [o for o in runs if str(o.get("status")) == "success"]
    rollback_count = sum(1 for o in applied_runs if bool(o.get("rollback_observed", False)))

    full_trace_runs = [o for o in runs if bool(o.get("has_trace_ref")) and bool(o.get("has_registry_ref"))]
    full_gate_trace_runs = [o for o in full_trace_runs if bool(o.get("has_gate_report"))]
    expectation_checked = [o for o in runs if bool(o.get("expectation_defined", False))]
    expectation_passed = [o for o in expectation_checked if bool(o.get("expectation_ok"))]
    expectations_pass_rate = (
        1.0 if len(expectation_checked) == 0 else (len(expectation_passed) / len(expectation_checked))
    )

    return {
        "runs_total": len(runs),
        "runs_success": sum(1 for o in runs if str(o.get("status")) == "success"),
        "runs_partial": sum(1 for o in runs if str(o.get("status")) == "partial"),
        "runs_failed": sum(1 for o in runs if str(o.get("status")) == "failed"),
        "runs_aborted": sum(1 for o in runs if str(o.get("status")) == "aborted"),
        "safe_fix_total": len(safe_fix_runs),
        "safe_fix_success": len(safe_fix_success),
        "safe_fix_success_rate": (len(safe_fix_success) / len(safe_fix_runs)) if safe_fix_runs else 0.0,
        "applied_total": len(applied_runs),
        "rollback_count": rollback_count,
        "rollback_rate": (rollback_count / len(applied_runs)) if applied_runs else 0.0,
        "full_trace_total": len(full_trace_runs),
        "full_gate_trace_total": len(full_gate_trace_runs),
        "full_trace_rate": (len(full_trace_runs) / len(runs)) if runs else 0.0,
        "full_gate_trace_rate": (len(full_gate_trace_runs) / len(runs)) if runs else 0.0,
        "expectations_total": len(expectation_checked),
        "expectations_passed": len(expectation_passed),
        "expectations_pass_rate": expectations_pass_rate,
    }


def _evaluate_release_gates(metrics: dict[str, Any]) -> dict[str, Any]:
    safe_fix_success_rate = float(metrics.get("safe_fix_success_rate") or 0.0)
    rollback_rate = float(metrics.get("rollback_rate") or 0.0)
    full_trace_rate = float(metrics.get("full_trace_rate") or 0.0)
    expectations_pass_rate = float(metrics.get("expectations_pass_rate") or 0.0)
    return {
        "safe_fix_success_rate_ge_70": safe_fix_success_rate >= 0.70,
        "rollback_rate_le_10": rollback_rate <= 0.10,
        "full_trace_rate_eq_100": full_trace_rate >= 1.0,
        "expectations_pass_rate_eq_100": expectations_pass_rate >= 1.0,
    }


def _collect_blockers(outcomes: list[dict[str, Any]], gates: dict[str, Any]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for gate, ok in gates.items():
        if not bool(ok):
            blockers.append({"type": "release_gate", "code": gate, "message": f"Gate failed: {gate}"})

    for item in outcomes:
        status = str(item.get("status") or "")
        expectation_defined = bool(item.get("expectation_defined", False))
        expectation_ok = bool(item.get("expectation_ok", False))
        if status in {"failed", "aborted"} and (not (expectation_defined and expectation_ok)):
            blockers.append(
                {
                    "type": "run_failure",
                    "task_id": item.get("task_id"),
                    "profile": item.get("profile"),
                    "run_id": item.get("run_id"),
                    "reason_code": item.get("reason_code"),
                }
            )
        if expectation_defined and (not expectation_ok):
            blockers.append(
                {
                    "type": "expectation_mismatch",
                    "task_id": item.get("task_id"),
                    "profile": item.get("profile"),
                    "errors": list(item.get("expectation_errors") or []),
                }
            )
    return blockers


def _prepare_case_workspace(workspace_root: Path, case: dict[str, Any]) -> str | None:
    setup_raw = case.get("setup")
    if not isinstance(setup_raw, dict):
        return None
    writes = setup_raw.get("write_files")
    if writes is None:
        return None
    if not isinstance(writes, list):
        return "setup.write_files must be list"

    for idx, raw in enumerate(writes):
        if not isinstance(raw, dict):
            return f"setup.write_files[{idx}] must be object"
        rel = raw.get("path")
        text = raw.get("text")
        if not isinstance(rel, str) or not rel.strip():
            return f"setup.write_files[{idx}].path must be non-empty string"
        if not isinstance(text, str):
            return f"setup.write_files[{idx}].text must be string"
        try:
            clean = _normalize_rel_path(rel)
        except Exception as e:  # noqa: BLE001
            return f"invalid setup path {rel!r}: {e}"
        dst = workspace_root / clean
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8")
    return None


def _normalize_rel_path(raw: str) -> Path:
    text = raw.replace("\\", "/").strip()
    p = PurePosixPath(text)
    if p.is_absolute():
        raise ValueError("absolute path is not allowed")
    if any(part in {"", ".", ".."} for part in p.parts):
        raise ValueError("path traversal is not allowed")
    return Path(*p.parts)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _digest_hash_map(items: dict[str, str]) -> str:
    h = hashlib.sha256()
    for key in sorted(items.keys()):
        line = f"{key}:{items[key]}\n".encode("utf-8", errors="replace")
        h.update(line)
    return h.hexdigest()
