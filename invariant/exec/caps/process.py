from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self

from invariant.exec.caps.audit import CapabilityAudit
from invariant.exec.caps.fs import FSAccess
from invariant.spec.capabilities import FilesystemCapabilityError, ProcessCapabilityError


class ProcessAccessError(ProcessCapabilityError): ...


class ProcessTimeoutLimitExceeded(ProcessAccessError): ...


class ProcessIdentityConflict(ProcessAccessError): ...


class _NoopSpan:
    def __enter__(self) -> Self:
        return self

    def add(self, **_fields: Any) -> None:
        return

    def __exit__(self, *_exc: object) -> Literal[False]:
        return False


@dataclass(frozen=True, slots=True)
class ProcessAccess:
    workspace: FSAccess
    max_timeout_s: float = 120.0
    max_output_chars: int = 40_000
    cap_id: str | None = None
    _audit: CapabilityAudit | None = None

    def __post_init__(self) -> None:
        if float(self.max_timeout_s) <= 0:
            raise ValueError("max_timeout_s must be > 0")
        if isinstance(self.max_output_chars, bool) or int(self.max_output_chars) <= 0:
            raise ValueError("max_output_chars must be > 0")

    def bind_audit(self, audit: CapabilityAudit, *, cap_id: str) -> Self:
        return replace(self, _audit=audit, cap_id=cap_id)

    def _span(self, op: str, payload: dict[str, Any]) -> Any:
        if self._audit is None:
            return _NoopSpan()
        return self._audit.span(
            f"cap.process.{op}",
            cap_id=(self.cap_id or "invariant:capability.<unknown>"),
            payload=payload,
        )

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path = ".",
        timeout_s: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        argv = self._normalize_command(command)
        cwd_rel, cwd_abs = self._resolve_cwd(cwd)
        timeout = self._effective_timeout(timeout_s)
        env_overrides = self._normalize_env(env)

        with self._span(
            "run",
            {
                "executable": Path(argv[0]).name or argv[0],
                "argc": len(argv),
                "cwd": cwd_rel,
                "timeout_s": timeout,
                "env_override_count": len(env_overrides),
                "max_output_chars": int(self.max_output_chars),
            },
        ) as sp:
            started = time.perf_counter()
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                completed = subprocess.run(
                    argv,
                    cwd=cwd_abs,
                    env={**os.environ, **env_overrides},
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    creationflags=creationflags,
                    shell=False,
                )
                returncode = int(completed.returncode)
                timed_out = False
                stdout = completed.stdout
                stderr = completed.stderr
            except subprocess.TimeoutExpired as exc:
                returncode = None
                timed_out = True
                stdout = self._coerce_text(exc.stdout)
                stderr = self._coerce_text(exc.stderr)
            except OSError as exc:
                raise ProcessAccessError(str(exc).strip() or "Process could not be started") from exc

            duration_ms = int((time.perf_counter() - started) * 1000)
            stdout_text, stdout_truncated = self._truncate(stdout)
            stderr_text, stderr_truncated = self._truncate(stderr)
            output = self._combine_output(stdout_text, stderr_text)
            sp.add(
                returncode=returncode,
                timed_out=timed_out,
                duration_ms=duration_ms,
                stdout_chars=len(stdout_text),
                stderr_chars=len(stderr_text),
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
            )
            return {
                "command": argv,
                "cwd": cwd_rel,
                "ok": (returncode == 0) and not timed_out,
                "returncode": returncode,
                "timed_out": timed_out,
                "timeout_s": timeout,
                "duration_ms": duration_ms,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "output": output,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            }

    def start(
        self,
        key: str,
        command: Sequence[str],
        *,
        cwd: str | Path = ".",
        env: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        normalized_key = self._normalize_key(key)
        argv = self._normalize_command(command)
        cwd_rel, cwd_abs = self._resolve_cwd(cwd)
        env_overrides = self._normalize_env(env)
        request_digest = self._request_digest(
            command=argv,
            cwd=cwd_rel,
            env=env_overrides,
        )
        handle = self._handle_for_key(normalized_key)
        paths = self._durable_paths(handle)
        initial = {
            "handle": handle,
            "request_digest": request_digest,
            "cwd": cwd_rel,
            "runner_pid": None,
            "child_pid": None,
            "status": "reserved",
            "running": False,
            "returncode": None,
            "reserved_at": self._utc_now(),
            "started_at": None,
            "finished_at": None,
            "reconciled_at": None,
            "stdout_truncated": False,
            "stderr_truncated": False,
            "error": None,
        }

        with self._span(
            "start",
            {
                "handle": handle,
                "executable": Path(argv[0]).name or argv[0],
                "argc": len(argv),
                "cwd": cwd_rel,
                "env_override_count": len(env_overrides),
                "max_output_chars": int(self.max_output_chars),
            },
        ) as sp:
            created = self._reserve_state(paths["state"], initial)
            if not created:
                existing = self._read_state(paths["state"])
                if str(existing.get("request_digest", "")) != request_digest:
                    raise ProcessIdentityConflict(
                        "process key is already bound to a different request"
                    )
                result = self.status(handle)
                result["reused"] = True
                sp.add(reused=True, status=result["status"])
                return result

            config = {
                "command": argv,
                "cwd_abs": str(cwd_abs),
                "env": env_overrides,
                "state_path": str(paths["state"]),
                "stdout_path": str(paths["stdout"]),
                "stderr_path": str(paths["stderr"]),
                "max_output_chars": int(self.max_output_chars),
            }
            self._write_json_atomic(paths["config"], config)

            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            )
            try:
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "invariant.exec.caps.process_worker",
                        str(paths["config"]),
                    ],
                    cwd=str(paths["dir"]),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    creationflags=creationflags,
                    start_new_session=(os.name != "nt"),
                )
            except OSError as exc:
                failed = dict(initial)
                failed["status"] = "spawn_error"
                failed["finished_at"] = self._utc_now()
                failed["error"] = {
                    "type": type(exc).__name__,
                    "message": str(exc).strip() or "Process worker could not be started.",
                }
                self._write_json_atomic(paths["state"], failed)
                raise ProcessAccessError(failed["error"]["message"]) from exc

            result = self.status(handle)
            result["reused"] = False
            sp.add(reused=False, status=result["status"])
            return result

    def status(self, handle: str) -> dict[str, Any]:
        normalized = self._normalize_handle(handle)
        paths = self._durable_paths(normalized)
        state = self._read_state(paths["state"])
        reconciled = self._reconcile_state(state, paths["state"])
        snapshot = self._snapshot(reconciled)
        with self._span(
            "status",
            {"handle": normalized},
        ) as sp:
            sp.add(status=snapshot["status"], running=snapshot["running"])
        return snapshot

    def terminate(
        self,
        handle: str,
        *,
        kill: bool = False,
        wait_s: float = 5.0,
    ) -> dict[str, Any]:
        normalized = self._normalize_handle(handle)
        wait_timeout = self._normalize_wait(wait_s)
        paths = self._durable_paths(normalized)
        state = self._reconcile_state(self._read_state(paths["state"]), paths["state"])
        child_pid = state.get("child_pid")

        with self._span(
            "terminate",
            {"handle": normalized, "kill": bool(kill), "wait_s": wait_timeout},
        ) as sp:
            if not isinstance(child_pid, int) or child_pid <= 0:
                result = self._snapshot(state)
                result["terminated"] = False
                result["kill"] = bool(kill)
                sp.add(terminated=False, status=result["status"])
                return result

            self._terminate_pid(child_pid, kill=bool(kill))
            deadline = time.perf_counter() + wait_timeout
            current = self.status(normalized)
            while current["running"] and time.perf_counter() < deadline:
                time.sleep(0.05)
                current = self.status(normalized)

            if current["running"] and os.name == "nt" and not bool(kill):
                self._terminate_pid(child_pid, kill=True)
                forced_deadline = time.perf_counter() + max(wait_timeout, 1.0)
                while current["running"] and time.perf_counter() < forced_deadline:
                    time.sleep(0.05)
                    current = self.status(normalized)

            current["terminated"] = not bool(current["running"])
            current["kill"] = bool(kill)
            sp.add(terminated=current["terminated"], status=current["status"])
            return current

    def read_output(
        self,
        handle: str,
        *,
        max_chars: int | None = None,
    ) -> dict[str, Any]:
        normalized = self._normalize_handle(handle)
        limit = self._effective_output_limit(max_chars)
        paths = self._durable_paths(normalized)
        state = self._read_state(paths["state"])
        stdout = self._read_bounded_text(paths["stdout"], limit)
        stderr = self._read_bounded_text(paths["stderr"], limit)
        stdout_text, stdout_limited = stdout
        stderr_text, stderr_limited = stderr
        snapshot = self._snapshot(state)
        snapshot.update(
            {
                "stdout": stdout_text,
                "stderr": stderr_text,
                "output": self._combine_output(stdout_text, stderr_text),
                "stdout_truncated": bool(state.get("stdout_truncated", False)) or stdout_limited,
                "stderr_truncated": bool(state.get("stderr_truncated", False)) or stderr_limited,
            }
        )
        return snapshot

    def _resolve_cwd(self, cwd: str | Path) -> tuple[str, Path]:
        raw = str(cwd).strip() or "."
        try:
            resolved = self.workspace.resolve(raw)
        except FilesystemCapabilityError as exc:
            raise ProcessAccessError(str(exc)) from exc

        if not resolved.exists():
            raise ProcessAccessError(f"Process cwd does not exist: {raw}")
        if not resolved.is_dir():
            raise ProcessAccessError(f"Process cwd is not a directory: {raw}")

        root = Path(self.workspace.root)
        relative = resolved.relative_to(root).as_posix()
        return (relative if relative else "."), resolved

    def _durable_paths(self, handle: str) -> dict[str, Path]:
        base = self.workspace.resolve(f".invariant/processes/{handle}")
        base.mkdir(parents=True, exist_ok=True)
        return {
            "dir": base,
            "config": base / "config.json",
            "state": base / "state.json",
            "stdout": base / "stdout.log",
            "stderr": base / "stderr.log",
        }

    @staticmethod
    def _normalize_key(key: str) -> str:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("process key must be a non-empty string")
        normalized = key.strip()
        if len(normalized) > 512:
            raise ValueError("process key must be <= 512 characters")
        return normalized

    @staticmethod
    def _handle_for_key(key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return f"proc_{digest[:32]}"

    @staticmethod
    def _normalize_handle(handle: str) -> str:
        if not isinstance(handle, str) or not handle.startswith("proc_"):
            raise ValueError("invalid process handle")
        suffix = handle[5:]
        if len(suffix) != 32 or any(ch not in "0123456789abcdef" for ch in suffix):
            raise ValueError("invalid process handle")
        return handle

    @staticmethod
    def _request_digest(
        *,
        command: Sequence[str],
        cwd: str,
        env: Mapping[str, str],
    ) -> str:
        payload = {
            "command": [str(item) for item in command],
            "cwd": str(cwd),
            "env": {str(key): str(env[key]) for key in sorted(env)},
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _reserve_state(path: Path, payload: Mapping[str, Any]) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(dict(payload), handle, ensure_ascii=True, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            return True
        except FileExistsError:
            return False

    @staticmethod
    def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
        temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        temp.write_text(
            json.dumps(dict(payload), ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        for attempt in range(10):
            try:
                os.replace(temp, path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.02)

    @staticmethod
    def _read_state(path: Path) -> dict[str, Any]:
        try:
            return dict(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError as exc:
            raise ProcessAccessError("process handle not found") from exc
        except (TypeError, ValueError) as exc:
            raise ProcessAccessError("invalid durable process state") from exc

    def _reconcile_state(self, state: Mapping[str, Any], state_path: Path) -> dict[str, Any]:
        reconciled = dict(state)
        status = str(reconciled.get("status", "")).strip() or "unknown"
        if status not in {"reserved", "starting", "running"}:
            return reconciled

        child_pid = reconciled.get("child_pid")
        runner_pid = reconciled.get("runner_pid")
        child_running = isinstance(child_pid, int) and self._pid_is_running(child_pid)
        runner_running = isinstance(runner_pid, int) and self._pid_is_running(runner_pid)

        changed = False
        if child_running:
            if status != "running":
                reconciled["status"] = "running"
                reconciled["running"] = True
                changed = True
        elif runner_running:
            if status == "reserved":
                reconciled["status"] = "starting"
                changed = True
        elif self._state_age_s(reconciled) >= 2.0:
            reconciled["status"] = "unknown"
            reconciled["running"] = False
            reconciled["reconciled_at"] = self._utc_now()
            reconciled["error"] = {
                "type": "ProcessOutcomeUnknown",
                "message": (
                    "Durable process state is in-flight but no live worker or child "
                    "can be confirmed. Automatic restart is forbidden."
                ),
            }
            changed = True

        if changed:
            self._write_json_atomic(state_path, reconciled)
        return reconciled

    @staticmethod
    def _snapshot(state: Mapping[str, Any]) -> dict[str, Any]:
        status = str(state.get("status", "")).strip() or "unknown"
        return {
            "handle": str(state.get("handle", "")).strip(),
            "command": [str(item) for item in state.get("command", [])],
            "cwd": str(state.get("cwd", "")).strip() or ".",
            "runner_pid": state.get("runner_pid"),
            "child_pid": state.get("child_pid"),
            "status": status,
            "running": status in {"reserved", "starting", "running"},
            "returncode": state.get("returncode"),
            "reserved_at": state.get("reserved_at"),
            "started_at": state.get("started_at"),
            "finished_at": state.get("finished_at"),
            "reconciled_at": state.get("reconciled_at"),
            "error": dict(state["error"]) if isinstance(state.get("error"), Mapping) else None,
        }

    @staticmethod
    def _state_age_s(state: Mapping[str, Any]) -> float:
        raw = state.get("started_at") or state.get("reserved_at")
        if not isinstance(raw, str) or not raw.strip():
            return float("inf")
        try:
            timestamp = datetime.fromisoformat(raw)
        except ValueError:
            return float("inf")
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - timestamp).total_seconds())

    @staticmethod
    def _pid_is_running(process_id: int) -> bool:
        if process_id <= 0:
            return False
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            query_limited_information = 0x1000
            still_active = 259
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.DWORD),
            ]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(query_limited_information, False, process_id)
            if not handle:
                return False
            try:
                exit_code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return int(exit_code.value) == still_active
            finally:
                kernel32.CloseHandle(handle)

        try:
            os.kill(process_id, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    @staticmethod
    def _terminate_pid(process_id: int, *, kill: bool) -> None:
        if os.name == "nt":
            if not kill:
                try:
                    os.kill(process_id, signal.SIGTERM)
                    return
                except OSError:
                    pass
            command = ["taskkill", "/PID", str(process_id), "/T", "/F"]
            subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
        os.kill(process_id, signal.SIGKILL if kill else signal.SIGTERM)

    def _effective_output_limit(self, value: int | None) -> int:
        if value is None:
            return int(self.max_output_chars)
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("max_chars must be an integer")
        if value <= 0:
            raise ValueError("max_chars must be > 0")
        return min(value, int(self.max_output_chars))

    @staticmethod
    def _read_bounded_text(path: Path, limit: int) -> tuple[str, bool]:
        if not path.exists():
            return "", False
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            text = handle.read(limit + 1)
        if len(text) <= limit:
            return text, False
        return text[:limit] + "\n...[truncated]", True

    @staticmethod
    def _normalize_wait(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("wait_s must be numeric")
        parsed = float(value)
        if parsed <= 0:
            raise ValueError("wait_s must be > 0")
        return parsed

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(UTC).isoformat()

    def _effective_timeout(self, timeout_s: float | None) -> float:
        if timeout_s is None:
            return float(self.max_timeout_s)
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
            raise TypeError("timeout_s must be numeric")
        value = float(timeout_s)
        if value <= 0:
            raise ValueError("timeout_s must be > 0")
        if value > float(self.max_timeout_s):
            raise ProcessTimeoutLimitExceeded(
                f"Requested timeout {value} exceeds max_timeout_s {self.max_timeout_s}"
            )
        return value

    @staticmethod
    def _normalize_command(command: Sequence[str]) -> list[str]:
        if not isinstance(command, Sequence) or isinstance(command, (str, bytes)):
            raise TypeError("command must be a sequence of strings")
        argv = [str(item).strip() for item in command]
        argv = [item for item in argv if item]
        if not argv:
            raise ValueError("command must not be empty")
        return argv

    @staticmethod
    def _normalize_env(env: Mapping[str, str] | None) -> dict[str, str]:
        if env is None:
            return {}
        if not isinstance(env, Mapping):
            raise TypeError("env must be a mapping of string keys and values")
        normalized: dict[str, str] = {}
        for key, value in env.items():
            normalized_key = str(key).strip()
            if not normalized_key:
                raise ValueError("env keys must be non-empty strings")
            normalized[normalized_key] = str(value)
        return normalized

    def _truncate(self, value: str) -> tuple[str, bool]:
        if len(value) <= int(self.max_output_chars):
            return value, False
        clipped = value[: int(self.max_output_chars)]
        return clipped + "\n...[truncated]", True

    @staticmethod
    def _combine_output(stdout: str, stderr: str) -> str:
        if stdout and stderr:
            return stdout + ("\n" if not stdout.endswith("\n") else "") + stderr
        return stdout or stderr

    @staticmethod
    def _coerce_text(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)
