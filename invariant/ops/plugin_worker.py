# invariant/ops/plugin_worker.py
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PROTO_STDIN = sys.stdin
_PROTO_STDOUT = sys.stdout

# Protocol fd: duplicate original stdout BEFORE we lock stdout down.
# We'll write JSON responses directly to this fd, so plugins cannot corrupt protocol.
# try:
#     _PROTO_OUT_FD: int | None = os.dup(1)
#     try:
#         # Do not leak protocol fd into child processes created by plugins.
#         os.set_inheritable(_PROTO_OUT_FD, False)
#     except Exception:
#         pass
# except Exception:
#     _PROTO_OUT_FD = None
_PROTO_OUT_FD: int | None
try:
    _PROTO_OUT_FD = os.dup(1)
except Exception:
    _PROTO_OUT_FD = None

if _PROTO_OUT_FD is not None:
    try:
        os.set_inheritable(_PROTO_OUT_FD, False)
    except Exception:
        pass


_WORKER_DEBUG = os.environ.get("INVARIANT_WORKER_DEBUG") in {"1", "true", "yes"}
_WORKER_FAULTHANDLER = os.environ.get("INVARIANT_WORKER_FAULTHANDLER") in {"1", "true", "yes"}


def _wlog(msg: str) -> None:
    if not _WORKER_DEBUG:
        return
    try:
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def _lockdown_worker_stdout() -> None:
    """
    Strict mode:
      - Permanently redirect OS-level stdout (fd=1) to stderr (fd=2)
      - Point sys.stdout to sys.stderr (Python-level)

    After this:
      - Any plugin prints to stdout go to stderr (and won't corrupt JSON protocol).
      - JSON protocol is still written via _PROTO_OUT_FD (saved original stdout).
    """
    # If we failed to duplicate stdout, we MUST NOT redirect fd=1:
    # the protocol depends on stdout in that case.
    if _PROTO_OUT_FD is None:
        # Still reduce accidental Python-level stdout noise.
        try:
            sys.stdout = sys.stderr
        except Exception:
            pass
        return
    try:
        try:
            sys.stdout.flush()
        except Exception:
            pass
        try:
            sys.stderr.flush()
        except Exception:
            pass

        os.dup2(2, 1)  # fd=1 -> fd=2
        sys.stdout = sys.stderr
    except Exception:
        # Best-effort; even if it fails, we still try to keep protocol clean via _PROTO_OUT_FD.
        pass


def _readline() -> str | None:
    line = _PROTO_STDIN.readline()
    if not line:
        return None
    return line.strip()


def _safe_json_dumps(obj: Any) -> str:
    """
    Protocol must never die because of non-serializable payload.
    """
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception as e:
        fallback = {
            "ok": False,
            "error": f"WorkerProtocolError: non-serializable response: {type(e).__name__}: {e}",
            "traceback": traceback.format_exc(),
        }
        return json.dumps(fallback, ensure_ascii=False)

def _os_write_all(fd: int, data: bytes) -> None:
    """
    Write all bytes to fd (handle partial writes).
    """
    view = memoryview(data)
    sent  = 0
    while sent  < len(view):
        try:
            n = os.write(fd, view[sent :])
        except InterruptedError:
            continue
        except BlockingIOError:
            continue
        if n <= 0:
            raise OSError("os.write returned 0 bytes")
        sent  += n


def _write(obj: Any) -> None:
    payload = _safe_json_dumps(obj)
    data = (payload + "\n").encode("utf-8", errors="replace")

    # Prefer raw fd write to the saved stdout (immune to plugin tampering).
    if _PROTO_OUT_FD is not None:
        _os_write_all(_PROTO_OUT_FD, data)
        return

    _PROTO_STDOUT.write(payload + "\n")
    _PROTO_STDOUT.flush()


@contextlib.contextmanager
def _plugin_stdout_to_stderr():
    """
    Keep stdout JSON protocol clean.
    Redirect BOTH:
      - Python-level sys.stdout -> sys.stderr
      - OS-level fd=1 -> fd=2 (best-effort; catches C-level prints)

    Limitations:
    - If plugin spawns background threads that print AFTER this context ends,
      they can still corrupt stdout. (We can harden further if you want.)
    """
    # NOTE:
    # With strict lockdown enabled (fd1->fd2 permanently), the OS-level redirect
    # below is mostly redundant, but harmless. Keep it for extra safety.

    # 1) Python-level redirect (covers print(), logging to sys.stdout, etc.)
    with contextlib.redirect_stdout(sys.stderr):
        # 2) OS-level redirect (covers C extensions writing to fd=1)
        saved_fd: int | None = None
        try:
            try:
                sys.stdout.flush()
            except Exception:
                pass
            try:
                sys.stderr.flush()
            except Exception:
                pass

            # Duplicate original stdout fd and redirect fd=1 -> fd=2
            saved_fd = os.dup(1)
            os.dup2(2, 1)
        except Exception:
            # Best-effort: if dup/dup2 fails on this platform, we still keep python redirect.
            saved_fd = None

        try:
            yield
        finally:
            if saved_fd is not None:
                try:
                    try:
                        sys.stderr.flush()
                    except Exception:
                        pass
                    os.dup2(saved_fd, 1)
                except Exception:
                    # Worst case: stdout stays redirected; protocol may break, but avoid crashing worker.
                    pass
                finally:
                    try:
                        os.close(saved_fd)
                    except Exception:
                        pass


def _load_module_from_path(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module spec: {module_name} from {file_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    # Keep stdout protocol clean during import side-effects.
    with _plugin_stdout_to_stderr():
        spec.loader.exec_module(mod)
    return mod


@dataclass
class PluginInstance:
    obj: Any


class Worker:
    def __init__(self) -> None:
        self._plugin: PluginInstance | None = None

    def op_load(self, *, entry: dict[str, Any]) -> dict[str, Any]:
        # entry: {"module":"pkg.mod" OR null, "file":"path/to/plugin.py", "class_name":"MyPlugin", "plugin_root": "..."}
        _wlog("op_load: start")
        plugin_root = Path(entry["plugin_root"]).resolve()
        file_s = entry.get("file")
        module_name = entry.get("module")
        cls_name = entry["class_name"]
        meta_payload = entry.get("meta")

        meta_obj: Any = None
        if isinstance(meta_payload, dict):
            try:
                # If invariant is available in worker PYTHONPATH, create real PluginMeta
                _wlog("op_load: import PluginMeta")
                from invariant.spec.meta import PluginMeta
                meta_obj = PluginMeta(**meta_payload)
            except Exception:
                meta_obj = meta_payload

        # Make plugin_root importable (plugin packages)
        if str(plugin_root) not in sys.path:
            sys.path.insert(0, str(plugin_root))
        _wlog(f"op_load: sys.path[0]={sys.path[0]}")

        if file_s:
            p = Path(str(file_s))
            file_path = (p if p.is_absolute() else (plugin_root / p)).resolve()
            # Basic safety: file must live under plugin_root (cross-platform)
            try:
                file_path.relative_to(plugin_root)
            except Exception:
                raise ValueError("plugin file must be under plugin_root") from None
            modname = module_name or f"_invariant_plugin_{cls_name}_{id(self)}"
            _wlog(f"op_load: load module name={modname} path={file_path}")
            mod = _load_module_from_path(modname, file_path)
        else:
            if not module_name:
                raise ValueError("Either 'file' or 'module' must be provided")
            with _plugin_stdout_to_stderr():
                mod = __import__(module_name, fromlist=["*"])

        _wlog("op_load: module loaded, resolve class")
        cls = getattr(mod, cls_name, None)
        if cls is None:
            raise AttributeError(f"Class not found: {cls_name}")

        # Constructor strategy: try meta/context injection, then fallback
        obj: Any
        with _plugin_stdout_to_stderr():
            try:
                _wlog("op_load: instantiate(meta, context)")
                obj = cls(meta=meta_obj, context=None)
            except TypeError:
                try:
                    _wlog("op_load: instantiate(meta)")
                    obj = cls(meta=meta_obj)
                except TypeError:
                    _wlog("op_load: instantiate()")
                    obj = cls()

        # Best-effort: attach meta attribute for convenience
        if meta_obj is not None:
            try:
                obj.meta = meta_obj
            except Exception:
                pass

        self._plugin = PluginInstance(obj=obj)
        _wlog("op_load: done")
        return {"ok": True}

    def op_call(self, *, method: str, args, kwargs) -> dict[str, Any]:
        if self._plugin is None:
            return {"ok": False, "error": "Plugin is not loaded"}
        fn = getattr(self._plugin.obj, method, None)
        if not callable(fn):
            return {"ok": False, "error": f"method not found: {method}"}

        with _plugin_stdout_to_stderr():
            out = fn(*list(args or []), **dict(kwargs or {}))

        # Ensure result is JSON-serializable; otherwise return a clean error instead of killing protocol.
        try:
            json.dumps(out, ensure_ascii=False)
        except Exception as e:
            return {
                "ok": False,
                "error": f"ResultNotSerializable: {type(e).__name__}: {e}",
                "traceback": traceback.format_exc(),
            }

        return {"ok": True, "result": out}

    def op_shutdown(self) -> dict[str, Any]:
        if self._plugin is not None:
            obj = self._plugin.obj
            try:
                fn = getattr(obj, "shutdown", None)
                if callable(fn):
                    with _plugin_stdout_to_stderr():
                        fn()
            except Exception:
                # worker shutdown should never crash protocol
                return {"ok": False, "error": "shutdown failed", "traceback": traceback.format_exc()}
        return {"ok": True}



def main() -> int:
    if _WORKER_FAULTHANDLER:
        try:
            import faulthandler

            try:
                interval = float(os.environ.get("INVARIANT_WORKER_FAULTHANDLER_INTERVAL", "10"))
            except Exception:
                interval = 10.0
            faulthandler.enable()
            faulthandler.dump_traceback_later(interval, repeat=True)
            _wlog(f"worker faulthandler enabled interval_s={interval}")
        except Exception as e:
            _wlog(f"worker faulthandler enable failed: {type(e).__name__}: {e}")

    _wlog(f"worker start pid={os.getpid()} proto_fd={_PROTO_OUT_FD}")
    # Lock stdout down ASAP so *all* plugin stdout (including background threads)
    # goes to stderr and cannot break the JSON protocol.
    #
    # IMPORTANT:
    # We can do strict OS-level lockdown safely ONLY if we have a dedicated protocol FD
    # (_PROTO_OUT_FD). Otherwise, we'd redirect fd=1 to stderr and the parent process
    # (which reads stdout for JSON lines) could stop receiving responses.
    if _PROTO_OUT_FD is not None:
        _lockdown_worker_stdout()
        _wlog("worker stdout lockdown: strict")
    else:
        # Can't redirect OS-level fd=1 safely (protocol depends on stdout).
        # But we can still reduce accidental stdout corruption from Python print().
        try:
            sys.stdout = sys.stderr
        except Exception:
            pass
        _wlog("worker stdout lockdown: compat")

    w = Worker()
    while True:
        line = _readline()
        if line is None:
            return 0
        if not line:
            continue
        msg: Any = None
        try:
            msg = json.loads(line)
            op = msg.get("op")
            mid = msg.get("id")
            _wlog(f"worker recv op={op!r} id={mid!r}")

            if op == "load":
                res = w.op_load(entry=msg["entry"])
            elif op == "call":
                res = w.op_call(method=msg["method"], args=msg.get("args"), kwargs=msg.get("kwargs"))
            elif op == "shutdown":
                res = w.op_shutdown()
                _write({"id": mid, **res})
                return 0
            else:
                raise ValueError(f"Unknown op: {op!r}")

            _wlog(f"worker send id={mid!r} ok={res.get('ok')}")
            _write({"id": mid, **res})
        except Exception as e:
            _wlog(f"worker exception: {type(e).__name__}: {e}")
            _write(
                {
                    "id": (msg.get("id") if isinstance(msg, dict) else None),
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                    "traceback": traceback.format_exc(),
                }
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
