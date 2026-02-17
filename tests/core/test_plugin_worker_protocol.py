# tests/test_plugin_worker_protocol.py
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path

import pytest


def _repo_root() -> Path:
    # tests/ -> repo root
    return Path(__file__).resolve().parents[1]


class WorkerClient:
    """
    Minimal client for invariant.ops.plugin_worker

    - Sends JSON requests to stdin
    - Reads stdout lines and scans until it finds a JSON dict with matching id
      (skips any junk lines that plugin may have printed, if any leak into stdout)
    """

    def __init__(self, *, python_exe: str, plugin_root: Path) -> None:
        self._lock = threading.Lock()
        self._stdout_tail: deque[str] = deque(maxlen=200)
        self._stderr_tail: deque[str] = deque(maxlen=200)

        core_root = _repo_root()
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")

        env["PYTHONPATH"] = os.pathsep.join(
            [str(core_root), str(plugin_root), env.get("PYTHONPATH", "")]
        )

        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        self._proc = subprocess.Popen(
            [python_exe, "-m", "invariant.ops.plugin_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            bufsize=1,
            creationflags=creationflags,
        )

        # Drain stderr in background for debugging
        t = threading.Thread(target=self._drain_stderr, daemon=True)
        t.start()

    def _drain_stderr(self) -> None:
        p = self._proc
        if p.stderr is None:
            return
        try:
            for line in p.stderr:
                self._stderr_tail.append(line.rstrip("\n"))
        except Exception:
            return

    def _hint(self) -> str:
        out = "\n".join(list(self._stdout_tail)[-30:])
        err = "\n".join(list(self._stderr_tail)[-80:])
        return (
            "\n--- stdout tail ---\n" + out +
            "\n--- stderr tail ---\n" + err
        )

    def request(self, msg: dict) -> dict:
        with self._lock:
            p = self._proc
            if p.poll() is not None:
                raise RuntimeError("worker is not running" + self._hint())

            assert p.stdin is not None
            assert p.stdout is not None

            req_id = msg.get("id")
            if not req_id:
                raise ValueError("request requires 'id'")

            p.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            p.stdin.flush()

            junk = 0
            for _ in range(300):
                line = p.stdout.readline()
                if not line:
                    raise RuntimeError("worker closed stdout without response" + self._hint())

                raw = line.strip()
                if not raw:
                    continue

                self._stdout_tail.append(raw)

                try:
                    resp = json.loads(raw)
                except Exception:
                    junk += 1
                    continue

                if not isinstance(resp, dict):
                    junk += 1
                    continue

                if resp.get("id") != req_id:
                    junk += 1
                    continue

                return resp

            raise RuntimeError(f"no valid response after scanning (junk={junk})" + self._hint())

    def close(self) -> None:
        p = self._proc
        if p.poll() is not None:
            return
        try:
            self.request({"op": "shutdown", "id": str(uuid.uuid4())})
        except Exception:
            pass
        try:
            p.terminate()
        except Exception:
            pass
        try:
            p.wait(timeout=2.0)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


def _write_plugin(plugin_root: Path, code: str) -> Path:
    plugin_root.mkdir(parents=True, exist_ok=True)
    p = plugin_root / "plugin.py"
    p.write_text(code, encoding="utf-8")
    return p


@pytest.fixture()
def plugin_root(tmp_path: Path) -> Path:
    return tmp_path / "my_plugin_root"


def test_worker_protocol_survives_stdout_noise(plugin_root: Path) -> None:
    plugin_code = r"""
import sys
import threading
import time

print("IMPORT NOISE: plugin module importing...")  # should never break protocol

class MyPlugin:
    def __init__(self, meta=None, context=None):
        self.meta = meta
        self.context = context

    def initialize(self, **deps):
        print("INIT NOISE:", deps)

    def ping(self, x):
        print("PING NOISE:", x)

        def bg():
            # background prints after the call returns
            for i in range(3):
                print(f"BG NOISE {i}")
                time.sleep(0.01)

        t = threading.Thread(target=bg, daemon=True)
        t.start()

        return {"ok": True, "echo": x, "unicode": "привет"}

    def echo(self, s):
        print("ECHO NOISE:", s)
        return s

    def shutdown(self):
        print("SHUTDOWN NOISE")
"""
    plugin_file = _write_plugin(plugin_root, plugin_code)

    c = WorkerClient(python_exe=sys.executable, plugin_root=plugin_root)
    try:
        # load
        rid = str(uuid.uuid4())
        resp = c.request(
            {
                "op": "load",
                "id": rid,
                "entry": {
                    "plugin_root": str(plugin_root),
                    "file": str(plugin_file),  # absolute
                    "module": None,
                    "class_name": "MyPlugin",
                    "meta": {"id": "t:noise", "version": "0.0.1"},
                },
            }
        )
        assert resp.get("ok") is True, resp

        # call ping (stdout noise + background noise)
        rid = str(uuid.uuid4())
        resp = c.request({"op": "call", "id": rid, "method": "ping", "args": ["hello"], "kwargs": {}})
        assert resp.get("ok") is True, resp
        assert resp["result"]["echo"] == "hello"
        assert resp["result"]["unicode"] == "привет"

        # unicode roundtrip
        rid = str(uuid.uuid4())
        resp = c.request({"op": "call", "id": rid, "method": "echo", "args": ["こんにちは"], "kwargs": {}})
        assert resp.get("ok") is True, resp
        assert resp["result"] == "こんにちは"

        # give BG thread a moment to print (should not break protocol)
        time.sleep(0.05)

        # another call after BG noise
        rid = str(uuid.uuid4())
        resp = c.request({"op": "call", "id": rid, "method": "echo", "args": ["after-bg"], "kwargs": {}})
        assert resp.get("ok") is True, resp
        assert resp["result"] == "after-bg"

    finally:
        c.close()


def test_worker_returns_clean_error_for_non_serializable_result(plugin_root: Path) -> None:
    plugin_code = r"""
class MyPlugin:
    def bad(self):
        # not JSON-serializable by default
        return set([1,2,3])
"""
    plugin_file = _write_plugin(plugin_root, plugin_code)

    c = WorkerClient(python_exe=sys.executable, plugin_root=plugin_root)
    try:
        rid = str(uuid.uuid4())
        resp = c.request(
            {
                "op": "load",
                "id": rid,
                "entry": {
                    "plugin_root": str(plugin_root),
                    "file": str(plugin_file),
                    "module": None,
                    "class_name": "MyPlugin",
                },
            }
        )
        assert resp.get("ok") is True, resp

        rid = str(uuid.uuid4())
        resp = c.request({"op": "call", "id": rid, "method": "bad", "args": [], "kwargs": {}})
        assert resp.get("ok") is False, resp
        assert "ResultNotSerializable" in (resp.get("error") or "")

    finally:
        c.close()


def test_worker_rejects_file_outside_plugin_root(tmp_path: Path) -> None:
    plugin_root = tmp_path / "root"
    plugin_root.mkdir(parents=True, exist_ok=True)

    outside = tmp_path / "outside.py"
    outside.write_text("class MyPlugin: pass\n", encoding="utf-8")

    c = WorkerClient(python_exe=sys.executable, plugin_root=plugin_root)
    try:
        rid = str(uuid.uuid4())
        resp = c.request(
            {
                "op": "load",
                "id": rid,
                "entry": {
                    "plugin_root": str(plugin_root),
                    "file": str(outside),  # outside root
                    "module": None,
                    "class_name": "MyPlugin",
                },
            }
        )
        assert resp.get("ok") is False, resp
        # message can vary, but should mention under plugin_root
        assert "plugin_root" in (resp.get("error") or "").lower() or "under" in (resp.get("error") or "").lower()

    finally:
        c.close()
