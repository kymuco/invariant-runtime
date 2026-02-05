# invariant/load/env/pip_ops.py
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PipResult:
    cmd: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class PipError(RuntimeError):
    def __init__(self, msg: str, *, result: PipResult) -> None:
        super().__init__(msg)
        self.result = result


log = logging.getLogger("invariant.pip")


def _kill_process_tree(p: subprocess.Popen, *, grace_s: float = 0.2) -> None:
    """
    Best-effort process tree termination.
    - POSIX: kill process group
    - Windows: taskkill /T /F
    """
    try:
        if p.poll() is not None:
            return
    except Exception:
        return

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(p.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            return
        except Exception:
            pass
        
        try:
            p.kill()
        except Exception:
            pass
        return

    # POSIX
    # try:
    #     import signal
    #     os.killpg(p.pid, signal.SIGTERM)
    #     time.sleep(0.1)
    #     if p.poll() is None:
    #         os.killpg(p.pid, signal.SIGKILL)
    # except Exception:
    #     try:
    #         p.kill()
    #     except Exception:
    #         pass
    # POSIX
    try:
        import signal

        killpg = getattr(os, "killpg", None)  # avoids mypy attr-defined on Windows
        if killpg is not None:
            sigterm: int = getattr(signal, "SIGTERM", 15)
            sigkill: int = getattr(signal, "SIGKILL", 9)  # на Windows атрибута нет — ок

            killpg(p.pid, sigterm)
            time.sleep(0.1)
            if p.poll() is None:
                killpg(p.pid, sigkill)
            return
    except Exception:
        pass

    # Fallback: try terminate/kill the parent if group kill isn't available
    try:
        p.terminate()
        time.sleep(grace_s)
    except Exception:
        pass
    try:
        if p.poll() is None:
            p.kill()
    except Exception:
        pass


def _tail(s: str, n: int = 2000) -> str:
    if not s:
        return ""
    return s[-n:]


def _run(
    cmd: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout_s: float | None = 900.0,
    env: dict[str, str] | None = None,
) -> PipResult:
    """
    Robust runner:
    - main thread polls => Ctrl+C is responsive
    - captures stdout/stderr via background threads
    - enforces timeout and kills process tree on timeout/interrupt
    """
    base_env = dict(os.environ)
    if env:
        base_env.update(env)

    # Make pip non-interactive and less noisy, consistent UTF-8.
    base_env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    base_env.setdefault("PIP_NO_INPUT", "1")
    base_env.setdefault("PIP_PROGRESS_BAR", "off")
    base_env.setdefault("PYTHONUTF8", "1")
    base_env.setdefault("PYTHONIOENCODING", "utf-8")

    popen_kwargs: dict[str, Any] = {
        "cwd": str(cwd) if cwd else None,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": base_env,
        "bufsize": 1,
    }

    if os.name == "nt":
        creationflags = 0
        creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True  # new process group

    t0 = time.monotonic()
    log.info("pip run: %s", " ".join(cmd))
    p = subprocess.Popen(list(cmd), **popen_kwargs)

    out_chunks: list[str] = []
    err_chunks: list[str] = []

    def _drain(stream, sink: list[str]) -> None:
        if stream is None:
            return
        try:
            for line in stream:
                sink.append(line)
        except Exception:
            return

    t_out = threading.Thread(target=_drain, args=(p.stdout, out_chunks), daemon=True)
    t_err = threading.Thread(target=_drain, args=(p.stderr, err_chunks), daemon=True)
    t_out.start()
    t_err.start()

    deadline: float | None = None
    if timeout_s is not None:
        try:
            tv = float(timeout_s)
        except Exception:
            tv = 0.0
        if tv > 0.0:
            deadline = time.monotonic() + tv

    try:
        while True:
            rc = p.poll()
            if rc is not None:
                break
            if deadline is not None and time.monotonic() >= deadline:
                _kill_process_tree(p)
                break
            time.sleep(0.05)
    except KeyboardInterrupt:
        _kill_process_tree(p)
        raise
    finally:
        try:
            p.wait(timeout=2.0)
        except Exception:
            _kill_process_tree(p)
            try:
                p.wait(timeout=2.0)
            except Exception:
                pass

    # give drain threads a moment
    try:
        t_out.join(timeout=1.0)
        t_err.join(timeout=1.0)
    except Exception:
        pass

    stdout = "".join(out_chunks)
    stderr = "".join(err_chunks)
    rc2 = int(p.returncode if p.returncode is not None else -9)
    res = PipResult(tuple(cmd), rc2, stdout, stderr)
    dt_ms = int((time.monotonic() - t0) * 1000)

    # timeout case: returncode might be None or still running; normalize to error
    if deadline is not None and time.monotonic() >= deadline and rc2 == -9:
        log.warning("pip timeout after %ss (rc=%s) cmd=%s", timeout_s, rc2, " ".join(res.cmd))
        raise PipError(
            f"pip timeout after {timeout_s}s: {' '.join(res.cmd)}\n"
            f"--- stdout tail ---\n{_tail(res.stdout)}\n"
            f"--- stderr tail ---\n{_tail(res.stderr)}",
            result=res,
        )

    if res.returncode != 0:
        log.warning("pip failed (rc=%s, dt_ms=%s) cmd=%s", res.returncode, dt_ms, " ".join(res.cmd))
        raise PipError(
            f"pip failed: {' '.join(res.cmd)}\n"
            f"--- stdout tail ---\n{_tail(res.stdout)}\n"
            f"--- stderr tail ---\n{_tail(res.stderr)}",
            result=res,
        )
    log.info("pip done (rc=%s, dt_ms=%s) cmd=%s", res.returncode, dt_ms, " ".join(res.cmd))
    return res


def pip_install_wheelhouse(
    *,
    python_exe: Path,
    requirements_path: Path,
    wheelhouse_dir: Path,
    timeout_s: float | None = 900.0,
) -> None:
    if not requirements_path.exists():
        raise FileNotFoundError(f"requirements file not found: {requirements_path}")
    if not wheelhouse_dir.exists():
        raise FileNotFoundError(f"wheelhouse_dir not found: {wheelhouse_dir}")

    cmd = [
        str(python_exe),
        "-m",
        "pip",
        "install",
        "--no-input",
        "--disable-pip-version-check",
        "--progress-bar",
        "off",
        "--no-index",
        "--find-links",
        str(wheelhouse_dir),
        "-r",
        str(requirements_path),
    ]
    _run(cmd, cwd=requirements_path.parent, timeout_s=timeout_s)

def pip_download_wheelhouse(
    *,
    python_exe: Path,
    requirements_path: Path,
    wheelhouse_dir: Path,
    index_url: str | None,
    extra_index_urls: tuple[str, ...],
    trusted_hosts: tuple[str, ...],
    only_binary: bool,
    timeout_s: float | None = 900.0,
    network_timeout_s: float = 30.0,
    retries: int = 3,
) -> None:
    wheelhouse_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(python_exe),
        "-m",
        "pip",
        "download",
        "--no-input",
        "--disable-pip-version-check",
        "--progress-bar",
        "off",
        "--retries",
        str(int(max(0, retries))),
        "--timeout",
        str(float(max(1.0, network_timeout_s))),
        "--dest",
        str(wheelhouse_dir),
        "-r",
        str(requirements_path),
    ]
    if index_url:
        cmd += ["--index-url", index_url]
    for u in extra_index_urls:
        cmd += ["--extra-index-url", str(u)]
    for h in trusted_hosts:
        cmd += ["--trusted-host", str(h)]
    if only_binary:
        cmd += ["--only-binary", ":all:"]

    _run(cmd, cwd=requirements_path.parent, timeout_s=timeout_s)
