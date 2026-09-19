from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, Self

from invariant.exec.caps.audit import CapabilityAudit
from invariant.exec.caps.fs import FSAccess
from invariant.spec.capabilities import FilesystemCapabilityError, ProcessCapabilityError


class ProcessAccessError(ProcessCapabilityError): ...


class ProcessTimeoutLimitExceeded(ProcessAccessError): ...


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
