from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

CONTRACT_TASK = "TaskSpec"
CONTRACT_PLAN = "PlanSpec"
CONTRACT_PATCH = "PatchSpec"
CONTRACT_RUN_REPORT = "RunReportSpec"
CONTRACT_GATE_REPORT = "GateReportSpec"

_ID_PATTERN = r"^[A-Za-z0-9._:-]{1,128}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"

PLAN_STEP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id", "name", "action"],
    "properties": {
        "id": {"type": "string", "minLength": 1, "maxLength": 128},
        "name": {"type": "string", "minLength": 1, "maxLength": 256},
        "action": {
            "type": "string",
            "enum": ["analyze", "patch", "gate", "report", "custom"],
        },
        "plugin": {"type": ["string", "null"]},
        "method": {"type": ["string", "null"]},
        "args": {"type": "array"},
        "kwargs": {"type": "object"},
        "depends_on": {"type": "array", "items": {"type": "string"}, "maxItems": 256},
        "continue_on_error": {"type": "boolean"},
        "metadata": {"type": "object"},
    },
    "additionalProperties": True,
}

PLAN_SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["plan_id", "task_id", "steps"],
    "properties": {
        "plan_id": {"type": "string", "pattern": _ID_PATTERN},
        "task_id": {"type": "string", "pattern": _ID_PATTERN},
        "steps": {
            "type": "array",
            "minItems": 1,
            "maxItems": 256,
            "items": PLAN_STEP_SCHEMA,
        },
        "metadata": {"type": "object"},
    },
    "additionalProperties": True,
}

PATCH_CHANGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["path"],
    "properties": {
        "path": {"type": "string", "minLength": 1},
        "op": {"type": "string", "enum": ["create", "update", "delete", "patch"]},
        "text": {"type": ["string", "null"]},
        "unified_diff": {"type": ["string", "null"], "minLength": 1},
        "expected_sha256": {"type": ["string", "null"], "pattern": _SHA256_PATTERN},
        "metadata": {"type": "object"},
    },
    "additionalProperties": True,
}

PATCH_SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["patch_id"],
    "properties": {
        "patch_id": {"type": "string", "pattern": _ID_PATTERN},
        "unified_diff": {"type": ["string", "null"], "minLength": 1},
        "changes": {
            "type": "array",
            "minItems": 1,
            "maxItems": 500,
            "items": PATCH_CHANGE_SCHEMA,
        },
        "metadata": {"type": "object"},
    },
    "anyOf": [{"required": ["unified_diff"]}, {"required": ["changes"]}],
    "additionalProperties": True,
}

TASK_SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["task_id", "goal"],
    "properties": {
        "task_id": {"type": "string", "pattern": _ID_PATTERN},
        "goal": {"type": "string", "minLength": 1, "maxLength": 4000},
        "repo_root": {"type": "string"},
        "inputs": {"type": "object"},
        "constraints": {"type": "array", "items": {"type": "string"}, "maxItems": 256},
        "plan": {"type": ["object", "null"]},
        "patch": {"type": ["object", "null"]},
        "gate": {"type": ["object", "null"]},
        "metadata": {"type": "object"},
    },
    "additionalProperties": True,
}

GATE_CHECK_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id", "command", "status", "duration_ms"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "command": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "status": {"type": "string", "enum": ["passed", "failed", "timeout", "error", "skipped"]},
        "duration_ms": {"type": "integer", "minimum": 0},
        "exit_code": {"type": "integer"},
        "stdout": {"type": "string"},
        "stderr": {"type": "string"},
        "allow_failure": {"type": "boolean"},
    },
    "additionalProperties": True,
}

GATE_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["gate_id", "status", "checks", "started_at_unix", "finished_at_unix", "duration_ms"],
    "properties": {
        "gate_id": {"type": "string", "pattern": _ID_PATTERN},
        "status": {"type": "string", "enum": ["passed", "failed", "error"]},
        "checks": {"type": "array", "items": GATE_CHECK_REPORT_SCHEMA, "maxItems": 256},
        "started_at_unix": {"type": "number"},
        "finished_at_unix": {"type": "number"},
        "duration_ms": {"type": "integer", "minimum": 0},
        "summary": {"type": "object"},
    },
    "additionalProperties": True,
}

RUN_STEP_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id", "name", "status", "started_at_unix", "finished_at_unix", "duration_ms"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "name": {"type": "string", "minLength": 1},
        "status": {"type": "string", "enum": ["success", "failed", "skipped"]},
        "started_at_unix": {"type": "number"},
        "finished_at_unix": {"type": "number"},
        "duration_ms": {"type": "integer", "minimum": 0},
        "error": {"type": ["object", "null"]},
        "artifact_key": {"type": ["string", "null"]},
    },
    "additionalProperties": True,
}

RUN_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["run_id", "task_id", "status", "started_at_unix", "finished_at_unix", "duration_ms", "steps"],
    "properties": {
        "run_id": {"type": "string", "pattern": _ID_PATTERN},
        "task_id": {"type": "string", "pattern": _ID_PATTERN},
        "status": {"type": "string", "enum": ["success", "partial", "failed", "aborted"]},
        "started_at_unix": {"type": "number"},
        "finished_at_unix": {"type": "number"},
        "duration_ms": {"type": "integer", "minimum": 0},
        "steps": {"type": "array", "items": RUN_STEP_REPORT_SCHEMA, "maxItems": 1024},
        "artifacts": {"type": "object"},
        "summary": {"type": "object"},
        "gate": {"type": ["object", "null"]},
    },
    "additionalProperties": True,
}

_SCHEMAS: dict[str, dict[str, Any]] = {
    CONTRACT_TASK: TASK_SPEC_SCHEMA,
    CONTRACT_PLAN: PLAN_SPEC_SCHEMA,
    CONTRACT_PATCH: PATCH_SPEC_SCHEMA,
    CONTRACT_GATE_REPORT: GATE_REPORT_SCHEMA,
    CONTRACT_RUN_REPORT: RUN_REPORT_SCHEMA,
}


@dataclass(frozen=True, slots=True)
class PlanStepSpec:
    id: str
    name: str
    action: str
    plugin: str | None = None
    method: str | None = None
    args: list[Any] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    continue_on_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlanSpec:
    plan_id: str
    task_id: str
    steps: list[PlanStepSpec]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PatchChangeSpec:
    path: str
    op: str = "update"
    text: str | None = None
    unified_diff: str | None = None
    expected_sha256: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PatchSpec:
    patch_id: str
    unified_diff: str | None = None
    changes: list[PatchChangeSpec] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TaskSpec:
    task_id: str
    goal: str
    repo_root: str = "."
    inputs: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    plan: dict[str, Any] | None = None
    patch: dict[str, Any] | None = None
    gate: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GateCheckReport:
    id: str
    command: list[str]
    status: str
    duration_ms: int
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    allow_failure: bool = False


@dataclass(frozen=True, slots=True)
class GateReportSpec:
    gate_id: str
    status: str
    checks: list[GateCheckReport]
    started_at_unix: float
    finished_at_unix: float
    duration_ms: int
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RunStepReport:
    id: str
    name: str
    status: str
    started_at_unix: float
    finished_at_unix: float
    duration_ms: int
    error: dict[str, Any] | None = None
    artifact_key: str | None = None


@dataclass(frozen=True, slots=True)
class RunReportSpec:
    run_id: str
    task_id: str
    status: str
    started_at_unix: float
    finished_at_unix: float
    duration_ms: int
    steps: list[RunStepReport]
    artifacts: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    gate: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def schema_for(contract_name: str) -> dict[str, Any]:
    try:
        return _SCHEMAS[contract_name]
    except KeyError as e:
        names = ", ".join(sorted(_SCHEMAS.keys()))
        raise KeyError(f"Unknown contract '{contract_name}'. Supported: {names}") from e


def validate_contract(
    contract_name: str,
    payload: Any,
    *,
    schema_guard: Any | None = None,
) -> dict[str, Any]:
    schema = schema_for(contract_name)
    if schema_guard is not None:
        _validate_with_schema_guard(contract_name, payload, schema_guard, schema)
        return {"ok": True, "via": "schema_guard"}
    _fallback_validate(contract_name, payload)
    return {"ok": True, "via": "fallback"}


def _validate_with_schema_guard(
    contract_name: str,
    payload: Any,
    schema_guard: Any,
    schema: Mapping[str, Any],
) -> None:
    validate_or_raise = getattr(schema_guard, "validate_or_raise", None)
    if callable(validate_or_raise):
        validate_or_raise(payload, dict(schema), root_name="$")
        return

    validate = getattr(schema_guard, "validate", None)
    if callable(validate):
        res = validate(payload, dict(schema), root_name="$")
        if not isinstance(res, Mapping):
            raise RuntimeError(
                f"schema_guard.validate returned non-mapping for {contract_name}"
            )
        if bool(res.get("ok", False)):
            return
        first = (res.get("errors") or [{}])[0]
        msg = first.get("message") or "schema validation failed"
        path = first.get("path") or "$"
        raise ValueError(f"{contract_name} invalid at {path}: {msg}")

    raise RuntimeError("schema_guard does not implement validate API")


def _fallback_validate(contract_name: str, payload: Any) -> None:
    if not isinstance(payload, Mapping):
        raise TypeError(f"{contract_name} payload must be an object")

    if contract_name == CONTRACT_TASK:
        _require_str(payload, "task_id")
        _require_str(payload, "goal")
        if "constraints" in payload and not _is_str_list(payload["constraints"]):
            raise ValueError("TaskSpec.constraints must be list[str]")
        return

    if contract_name == CONTRACT_PLAN:
        _require_str(payload, "plan_id")
        _require_str(payload, "task_id")
        steps = payload.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError("PlanSpec.steps must be non-empty list")
        for idx, raw in enumerate(steps):
            if not isinstance(raw, Mapping):
                raise ValueError(f"PlanSpec.steps[{idx}] must be object")
            _require_str(raw, "id")
            _require_str(raw, "name")
            _require_str(raw, "action")
        return

    if contract_name == CONTRACT_PATCH:
        _require_str(payload, "patch_id")
        has_diff = isinstance(payload.get("unified_diff"), str) and bool(payload["unified_diff"].strip())
        has_changes = isinstance(payload.get("changes"), list) and len(payload["changes"]) > 0
        if not (has_diff or has_changes):
            raise ValueError("PatchSpec requires unified_diff or changes")
        return

    if contract_name == CONTRACT_GATE_REPORT:
        _require_str(payload, "gate_id")
        _require_str(payload, "status")
        checks = payload.get("checks")
        if not isinstance(checks, list):
            raise ValueError("GateReportSpec.checks must be list")
        return

    if contract_name == CONTRACT_RUN_REPORT:
        _require_str(payload, "run_id")
        _require_str(payload, "task_id")
        _require_str(payload, "status")
        steps = payload.get("steps")
        if not isinstance(steps, list):
            raise ValueError("RunReportSpec.steps must be list")
        return

    raise KeyError(contract_name)


def coerce_task_spec(payload: Mapping[str, Any], *, schema_guard: Any | None = None) -> TaskSpec:
    validate_contract(CONTRACT_TASK, payload, schema_guard=schema_guard)
    return TaskSpec(
        task_id=str(payload["task_id"]).strip(),
        goal=str(payload["goal"]),
        repo_root=str(payload.get("repo_root", ".")),
        inputs=_coerce_dict(payload.get("inputs")),
        constraints=_coerce_str_list(payload.get("constraints")),
        plan=_coerce_obj_or_none(payload.get("plan")),
        patch=_coerce_obj_or_none(payload.get("patch")),
        gate=_coerce_obj_or_none(payload.get("gate")),
        metadata=_coerce_dict(payload.get("metadata")),
    )


def coerce_plan_spec(payload: Mapping[str, Any], *, schema_guard: Any | None = None) -> PlanSpec:
    validate_contract(CONTRACT_PLAN, payload, schema_guard=schema_guard)
    steps_raw = payload.get("steps") or []
    steps: list[PlanStepSpec] = []
    for idx, raw in enumerate(steps_raw):
        if not isinstance(raw, Mapping):
            raise ValueError(f"PlanSpec.steps[{idx}] must be object")
        steps.append(
            PlanStepSpec(
                id=str(raw["id"]).strip(),
                name=str(raw["name"]),
                action=str(raw["action"]),
                plugin=_coerce_str_or_none(raw.get("plugin")),
                method=_coerce_str_or_none(raw.get("method")),
                args=_coerce_list(raw.get("args")),
                kwargs=_coerce_dict(raw.get("kwargs")),
                depends_on=_coerce_str_list(raw.get("depends_on")),
                continue_on_error=bool(raw.get("continue_on_error", False)),
                metadata=_coerce_dict(raw.get("metadata")),
            )
        )
    return PlanSpec(
        plan_id=str(payload["plan_id"]).strip(),
        task_id=str(payload["task_id"]).strip(),
        steps=steps,
        metadata=_coerce_dict(payload.get("metadata")),
    )


def coerce_patch_spec(payload: Mapping[str, Any], *, schema_guard: Any | None = None) -> PatchSpec:
    validate_contract(CONTRACT_PATCH, payload, schema_guard=schema_guard)
    changes_raw = payload.get("changes") or []
    changes: list[PatchChangeSpec] = []
    for idx, raw in enumerate(changes_raw):
        if not isinstance(raw, Mapping):
            raise ValueError(f"PatchSpec.changes[{idx}] must be object")
        changes.append(
            PatchChangeSpec(
                path=str(raw["path"]),
                op=str(raw.get("op", "update")),
                text=_coerce_str_or_none(raw.get("text")),
                unified_diff=_coerce_str_or_none(raw.get("unified_diff")),
                expected_sha256=_coerce_str_or_none(raw.get("expected_sha256")),
                metadata=_coerce_dict(raw.get("metadata")),
            )
        )
    return PatchSpec(
        patch_id=str(payload["patch_id"]).strip(),
        unified_diff=_coerce_str_or_none(payload.get("unified_diff")),
        changes=changes,
        metadata=_coerce_dict(payload.get("metadata")),
    )


def coerce_gate_report_spec(
    payload: Mapping[str, Any],
    *,
    schema_guard: Any | None = None,
) -> GateReportSpec:
    validate_contract(CONTRACT_GATE_REPORT, payload, schema_guard=schema_guard)
    checks: list[GateCheckReport] = []
    for idx, raw in enumerate(payload.get("checks") or []):
        if not isinstance(raw, Mapping):
            raise ValueError(f"GateReportSpec.checks[{idx}] must be object")
        checks.append(
            GateCheckReport(
                id=str(raw["id"]),
                command=[str(x) for x in (raw.get("command") or [])],
                status=str(raw["status"]),
                duration_ms=int(raw["duration_ms"]),
                exit_code=int(raw["exit_code"]) if raw.get("exit_code") is not None else None,
                stdout=_coerce_str_or_none(raw.get("stdout")),
                stderr=_coerce_str_or_none(raw.get("stderr")),
                allow_failure=bool(raw.get("allow_failure", False)),
            )
        )
    return GateReportSpec(
        gate_id=str(payload["gate_id"]),
        status=str(payload["status"]),
        checks=checks,
        started_at_unix=float(payload["started_at_unix"]),
        finished_at_unix=float(payload["finished_at_unix"]),
        duration_ms=int(payload["duration_ms"]),
        summary=_coerce_dict(payload.get("summary")),
    )


def coerce_run_report_spec(
    payload: Mapping[str, Any],
    *,
    schema_guard: Any | None = None,
) -> RunReportSpec:
    validate_contract(CONTRACT_RUN_REPORT, payload, schema_guard=schema_guard)
    steps: list[RunStepReport] = []
    for idx, raw in enumerate(payload.get("steps") or []):
        if not isinstance(raw, Mapping):
            raise ValueError(f"RunReportSpec.steps[{idx}] must be object")
        err_raw = raw.get("error")
        err = dict(err_raw) if isinstance(err_raw, Mapping) else None
        steps.append(
            RunStepReport(
                id=str(raw["id"]),
                name=str(raw["name"]),
                status=str(raw["status"]),
                started_at_unix=float(raw["started_at_unix"]),
                finished_at_unix=float(raw["finished_at_unix"]),
                duration_ms=int(raw["duration_ms"]),
                error=err,
                artifact_key=_coerce_str_or_none(raw.get("artifact_key")),
            )
        )
    gate_raw = payload.get("gate")
    gate = dict(gate_raw) if isinstance(gate_raw, Mapping) else None
    return RunReportSpec(
        run_id=str(payload["run_id"]),
        task_id=str(payload["task_id"]),
        status=str(payload["status"]),
        started_at_unix=float(payload["started_at_unix"]),
        finished_at_unix=float(payload["finished_at_unix"]),
        duration_ms=int(payload["duration_ms"]),
        steps=steps,
        artifacts=_coerce_dict(payload.get("artifacts")),
        summary=_coerce_dict(payload.get("summary")),
        gate=gate,
    )


def _require_str(obj: Mapping[str, Any], key: str) -> None:
    v = obj.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{key} must be non-empty string")


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def _coerce_dict(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return dict(raw)
    raise ValueError("expected object")


def _coerce_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return list(raw)
    raise ValueError("expected list")


def _coerce_str_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("expected list[str]")
    out: list[str] = []
    for x in raw:
        if not isinstance(x, str):
            raise ValueError("expected list[str]")
        out.append(x)
    return out


def _coerce_obj_or_none(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        return dict(raw)
    raise ValueError("expected object or null")


def _coerce_str_or_none(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    return str(raw)
