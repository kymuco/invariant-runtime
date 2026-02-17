from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Any

import pytest

import invariant


def _core_root() -> Path:
    # invariant/__init__.py -> invariant package dir -> project root-ish
    return Path(invariant.__file__).resolve().parents[1]


def _readline_with_timeout(stream, timeout_s: float) -> str:
    """
    Cross-platform "readline with timeout" to avoid hanging tests on a broken worker.
    """
    q: Queue[str | None] = Queue()

    def _reader():
        try:
            line = stream.readline()
        except Exception:
            line = ""
        q.put(line)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    try:
        line = q.get(timeout=timeout_s)
    except Empty as e:
        raise TimeoutError("timeout waiting for worker stdout") from e

    if not line:
        return ""
    return line


@dataclass
class WorkerClient:
    proc: subprocess.Popen[str]
    stdin: Any
    stdout: Any

    def request(self, msg: dict[str, Any], *, timeout_s: float = 5.0) -> dict[str, Any]:
        req_id = msg.get("id")
        assert req_id, "request requires 'id'"

        payload = json.dumps(msg, ensure_ascii=False) + "\n"
        self.stdin.write(payload)
        self.stdin.flush()

        line = _readline_with_timeout(self.stdout, timeout_s=timeout_s)
        if not line:
            raise RuntimeError("worker returned empty stdout (crash?)")

        try:
            resp = json.loads(line.strip())
        except Exception as e:
            raise RuntimeError(f"worker returned non-json: {line!r}") from e

        assert isinstance(resp, dict)
        # protocol must match id
        assert resp.get("id") == req_id, f"mismatched response id: {resp.get('id')} != {req_id}"
        return resp

    def shutdown(self) -> None:
        try:
            self.request({"op": "shutdown", "id": "shutdown"}, timeout_s=3.0)
        except Exception:
            pass
        try:
            self.proc.terminate()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=2.0)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


def spawn_worker(*, plugin_root: Path) -> WorkerClient:
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    # Force UTF-8 everywhere (Windows is the usual troublemaker)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    # Make sure worker can import invariant (core) and plugin packages
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_core_root()), str(plugin_root), env.get("PYTHONPATH", "")]
    )

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    p = subprocess.Popen(
        [sys.executable, "-m", "invariant.ops.plugin_worker"],
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
    assert p.stdin is not None
    assert p.stdout is not None
    return WorkerClient(proc=p, stdin=p.stdin, stdout=p.stdout)


def write_plugin(plugin_root: Path, *, name: str, code: str) -> Path:
    plugin_root.mkdir(parents=True, exist_ok=True)
    path = plugin_root / name
    path.write_text(code, encoding="utf-8")
    return path


def _entry(*, plugin_root: Path, file_path: Path, class_name: str, module: str | None = None) -> dict[str, Any]:
    return {
        "plugin_root": str(plugin_root),
        "file": str(file_path),
        "module": module,
        "class_name": class_name,
        "meta": {"id": "test:plugin", "version": "0.0.0+test", "name": "Тест"},
    }


@pytest.fixture()
def tmp_plugin_root(tmp_path: Path) -> Path:
    return tmp_path / "plugin_root"


def test_worker_protocol_survives_stdout_noise(tmp_plugin_root: Path) -> None:
    """
    Plugin prints to stdout on import and during calls.
    Worker must keep protocol JSON clean.
    """
    plugin_file = write_plugin(
        tmp_plugin_root,
        name="noisy_plugin.py",
        code=(
            "print('NOISE: import-time print to stdout')\n"
            "class Noisy:\n"
            "    def initialize(self, **deps):\n"
            "        print('NOISE: initialize() print')\n"
            "        return None\n"
            "    def ping(self, x):\n"
            "        print('NOISE: ping() print', x)\n"
            "        return {'ok': True, 'x': x}\n"
        ),
    )

    w = spawn_worker(plugin_root=tmp_plugin_root)
    try:
        r1 = w.request(
            {"op": "load", "id": "1", "entry": _entry(plugin_root=tmp_plugin_root, file_path=plugin_file, class_name="Noisy")}
        )
        assert r1.get("ok") is True, r1

        r2 = w.request({"op": "call", "id": "2", "method": "ping", "args": [123], "kwargs": {}})
        assert r2.get("ok") is True, r2
        assert r2.get("result") == {"ok": True, "x": 123}
    finally:
        w.shutdown()


def test_worker_unicode_roundtrip(tmp_plugin_root: Path) -> None:
    plugin_file = write_plugin(
        tmp_plugin_root,
        name="unicode_plugin.py",
        code=(
            "class Unicode:\n"
            "    def hello(self, name):\n"
            "        return f'Привет, {name}!'\n"
        ),
    )

    w = spawn_worker(plugin_root=tmp_plugin_root)
    try:
        r1 = w.request(
            {"op": "load", "id": "1", "entry": _entry(plugin_root=tmp_plugin_root, file_path=plugin_file, class_name="Unicode")}
        )
        assert r1.get("ok") is True, r1

        r2 = w.request({"op": "call", "id": "2", "method": "hello", "args": ["мир"], "kwargs": {}})
        assert r2.get("ok") is True, r2
        assert r2.get("result") == "Привет, мир!"
    finally:
        w.shutdown()


def test_worker_result_not_serializable_is_clean_error(tmp_plugin_root: Path) -> None:
    """
    If plugin returns something JSON can't serialize, worker must respond ok=False
    instead of crashing the protocol.
    """
    plugin_file = write_plugin(
        tmp_plugin_root,
        name="bad_result.py",
        code=(
            "class Bad:\n"
            "    def give_set(self):\n"
            "        return {1, 2, 3}\n"
        ),
    )

    w = spawn_worker(plugin_root=tmp_plugin_root)
    try:
        r1 = w.request(
            {"op": "load", "id": "1", "entry": _entry(plugin_root=tmp_plugin_root, file_path=plugin_file, class_name="Bad")}
        )
        assert r1.get("ok") is True, r1

        r2 = w.request({"op": "call", "id": "2", "method": "give_set", "args": [], "kwargs": {}})
        assert r2.get("ok") is False, r2
        assert "ResultNotSerializable" in (r2.get("error") or "")
    finally:
        w.shutdown()


def test_worker_rejects_file_outside_plugin_root(tmp_plugin_root: Path, tmp_path: Path) -> None:
    """
    Security: file must be under plugin_root.
    """
    outside = tmp_path / "outside.py"
    outside.write_text("class X: pass\n", encoding="utf-8")

    w = spawn_worker(plugin_root=tmp_plugin_root)
    try:
        r1 = w.request(
            {"op": "load", "id": "1", "entry": _entry(plugin_root=tmp_plugin_root, file_path=outside, class_name="X")}
        )
        # worker catches exception and returns ok=False
        assert r1.get("ok") is False, r1
        assert "plugin file must be under plugin_root" in (r1.get("error") or "").lower()
    finally:
        w.shutdown()
