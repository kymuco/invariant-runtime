from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _read_state(path: Path) -> dict[str, Any]:
    for attempt in range(10):
        try:
            return dict(json.loads(path.read_text(encoding="utf-8")))
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.02)
    raise AssertionError("unreachable")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    for attempt in range(10):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.02)


def _drain(
    stream: TextIO,
    target: Path,
    *,
    max_chars: int,
    truncated: dict[str, bool],
    key: str,
) -> None:
    written = 0
    with target.open("w", encoding="utf-8", errors="replace") as handle:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            remaining = max(0, max_chars - written)
            if remaining:
                piece = chunk[:remaining]
                handle.write(piece)
                handle.flush()
                written += len(piece)
            if len(chunk) > remaining:
                truncated[key] = True


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Expected one process config path")

    config_path = Path(sys.argv[1]).resolve()
    config = dict(json.loads(config_path.read_text(encoding="utf-8")))
    try:
        config_path.unlink()
    except OSError:
        pass
    state_path = Path(str(config["state_path"])).resolve()
    stdout_path = Path(str(config["stdout_path"])).resolve()
    stderr_path = Path(str(config["stderr_path"])).resolve()

    state = _read_state(state_path)
    state["runner_pid"] = os.getpid()
    state["status"] = "starting"
    state["running"] = False
    state["error"] = None
    _write_json_atomic(state_path, state)

    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in dict(config.get("env", {})).items()})
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess,
        "CREATE_NEW_PROCESS_GROUP",
        0,
    )

    try:
        child = subprocess.Popen(
            [str(item) for item in config["command"]],
            cwd=str(config["cwd_abs"]),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            creationflags=creationflags,
            start_new_session=(os.name != "nt"),
        )
    except OSError as exc:
        state = _read_state(state_path)
        state["status"] = "spawn_error"
        state["running"] = False
        state["returncode"] = None
        state["finished_at"] = _utc_now()
        state["error"] = {
            "type": type(exc).__name__,
            "message": str(exc).strip() or "Process could not be started.",
        }
        _write_json_atomic(state_path, state)
        return 1

    state = _read_state(state_path)
    state["child_pid"] = child.pid
    state["status"] = "running"
    state["running"] = True
    state["started_at"] = _utc_now()
    _write_json_atomic(state_path, state)

    max_chars = int(config.get("max_output_chars", 40_000))
    truncated = {"stdout": False, "stderr": False}
    stdout_thread = threading.Thread(
        target=_drain,
        args=(child.stdout, stdout_path),
        kwargs={"max_chars": max_chars, "truncated": truncated, "key": "stdout"},
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_drain,
        args=(child.stderr, stderr_path),
        kwargs={"max_chars": max_chars, "truncated": truncated, "key": "stderr"},
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    returncode = child.wait()
    stdout_thread.join()
    stderr_thread.join()

    state = _read_state(state_path)
    state["status"] = "exited"
    state["running"] = False
    state["returncode"] = int(returncode)
    state["finished_at"] = _utc_now()
    state["stdout_truncated"] = bool(truncated["stdout"])
    state["stderr_truncated"] = bool(truncated["stderr"])
    state["error"] = None
    _write_json_atomic(state_path, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
