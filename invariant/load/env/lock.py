# invariant/load/env/lock.py
from __future__ import annotations

import json
import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class FileLock:
    """
    Cross-process lock via atomic file create.
    Good enough for build/venv install gating.

    Not re-entrant. Not crash-proof forever (we use stale TTL).
    """
    path: Path
    stale_after_s: float = 30 * 60.0  # 30 minutes
    _token: str | None = None
    _held: bool = False

    def _now(self) -> float:
        return time.time()

    def _host(self) -> str:
        try:
            return socket.gethostname()
        except Exception:
            return "unknown"

    def _read_lock_info(self) -> dict[str, Any] | None:
        try:
            raw = self.path.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            return None
        if not raw:
            return None

        # new format: json
        if raw.startswith("{"):
            try:
                v = json.loads(raw)
                if isinstance(v, dict):
                    return v
            except Exception:
                return None

        # old format: pid-only string
        try:
            pid = int(raw)
        except Exception:
            return None
        return {"pid": pid, "token": None, "created_at": None, "host": None}

    def _pid_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name != "nt":
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False
            except Exception:
                return True  # best-effort: don't delete locks aggressively

        # Windows: check process exists + exit code
        try:
            import ctypes  # stdlib

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            OpenProcess = ctypes.windll.kernel32.OpenProcess
            GetExitCodeProcess = ctypes.windll.kernel32.GetExitCodeProcess
            CloseHandle = ctypes.windll.kernel32.CloseHandle

            h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
            if not h:
                return False
            try:
                code = ctypes.c_ulong(0)
                if not GetExitCodeProcess(h, ctypes.byref(code)):
                    return True
                return int(code.value) == STILL_ACTIVE
            finally:
                try:
                    CloseHandle(h)
                except Exception:
                    pass
        except Exception:
            # best-effort: if we can't check, do not assume dead
            return True

    def _is_stale(self) -> tuple[bool, str]:
        """
        Returns (is_stale, reason).
        """
        try:
            st = self.path.stat()
            age = self._now() - st.st_mtime
        except Exception:
            return (False, "stat_failed")

        info = self._read_lock_info() or {}
        pid = info.get("pid")
        try:
            pid_i = int(pid) if pid is not None else None
        except Exception:
            pid_i = None

        # If process is definitely dead -> stale immediately
        if pid_i is not None and not self._pid_alive(pid_i):
            return (True, f"owner_pid_dead pid={pid_i}")

        # Otherwise rely on TTL
        if age > float(self.stale_after_s):
            return (True, f"ttl_expired age_s={age:.1f}")

        return (False, f"fresh age_s={age:.1f}")

    def _describe_owner(self) -> str:
        info = self._read_lock_info() or {}
        pid = info.get("pid")
        host = info.get("host")
        created = info.get("created_at")
        token = info.get("token")
        return f"pid={pid!r} host={host!r} created_at={created!r} token={token!r}"

    def acquire(self, *, timeout_s: float = 300.0, poll_s: float = 0.1) -> None:
        # Use monotonic for waiting logic (resistant to system clock changes).
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._token = str(uuid.uuid4())
        log = logging.getLogger("invariant.env.lock")
        last_log = 0.0

        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    payload = {
                        "pid": os.getpid(),
                        "host": self._host(),
                        "created_at": self._now(),
                        "token": self._token,
                    }
                    f.write(json.dumps(payload, ensure_ascii=False))
                self._held = True
                return
            except FileExistsError:
                stale, reason = self._is_stale()
                if stale:
                    try:
                        self.path.unlink(missing_ok=True)
                        log.warning("Removed stale lock %s (%s)", self.path, reason)
                    except Exception:
                        # If can't remove, we'll just wait.
                        pass
                else:
                    now = time.monotonic()
                    if (now - last_log) >= 5.0:
                        last_log = now
                        log.info("Waiting for lock %s owner=(%s)", self.path, self._describe_owner())

                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Lock timeout: {self.path} owner=({self._describe_owner()})"
                    ) from None

                time.sleep(max(0.01, float(poll_s)))

    def release(self) -> None:
        try:
            # Safety: remove only if we still own it (token matches),
            # otherwise do not delete another process's lock.
            info = self._read_lock_info() or {}
            if self._token is None:
                return
            if info.get("token") != self._token:
                return
            self.path.unlink(missing_ok=True)
        except Exception:
            pass
        finally:
            self._held = False
            self._token = None

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
