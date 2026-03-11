# invariant/load/isolated_proxy.py
from __future__ import annotations

import concurrent.futures as cf
import json
import logging
import os
import subprocess
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

import invariant  # noqa: F401
from invariant.load.env.env_manager import EnvManager
from invariant.load.rpc_codec import assert_rpc_json_serializable, decode_rpc_value
from invariant.spec.deps import PluginDepsSpec
from invariant.spec.descriptor import PluginDescriptor
from invariant.spec.errors import PluginLoadError
from invariant.spec.plugin import BasePlugin

log = logging.getLogger("invariant.isolated.worker")

# Shared pool to enforce hard call timeouts without blocking the caller thread.
_REQUEST_POOL = cf.ThreadPoolExecutor(
    max_workers=8,
    thread_name_prefix="invariant-req",
)

class _WorkerClient:
    def __init__(self, *, python_exe: Path, plugin_root: Path, name: str) -> None:
        # ЗАМЕНА: self._lock = threading.Lock() на специализированный лок:
        self._write_lock = threading.Lock() # Защищает только p.stdin.write
        self._state_lock = threading.Lock() # Защищает флаг _closed
        self._name = name
        self._cv = threading.Condition()
        self._responses: dict[str, dict[str, Any]] = {}
        self._stderr_tail: deque[str] = deque(maxlen=200)
        self._stdout_tail: deque[str] = deque(maxlen=200)
        self._closed = False
        self._proc = self._spawn(python_exe=python_exe, plugin_root=plugin_root)
        self._start_stderr_drain()
        self._start_stdout_drain()

    def _spawn(self, *, python_exe: Path, plugin_root: Path) -> subprocess.Popen:
        # need invariant importable for "-m invariant.ops.plugin_worker"
        core_root = Path(invariant.__file__).resolve().parents[1]  # .../invariant -> project root-ish

        # Hardened env: собираем с нуля, прокидываем только нужное.
        # Это снижает риск утечки секретов/нестабильного поведения из parent process env.
        safe_keys = {
            # Базовые системные переменные (кроссплатформенно)
            "PATH",
            "SystemRoot", "WINDIR",          # Windows
            "HOME", "USERPROFILE",           # Unix / Windows
            "TMP", "TEMP", "TMPDIR",         # temp dirs

            # Locale (иногда влияет на кодировки/поведение библиотек)
            "LANG", "LC_ALL", "LC_CTYPE",

            # SSL / CA bundles (если плагины/воркер ходят в сеть)
            "SSL_CERT_FILE", "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",

            # Proxy (если окружение требует)
            "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "no_proxy",

            # Native library paths (аккуратно: могут понадобиться C-расширениям)
            "LD_LIBRARY_PATH",               # Linux
            "DYLD_LIBRARY_PATH",             # macOS (если применимо)

            # Ваши debug-флаги (если хотите сохранять их поведение)
            "INVARIANT_WORKER_DEBUG",
            "INVARIANT_WORKER_FAULTHANDLER",
        }

        env = {k: os.environ[k] for k in safe_keys if k in os.environ}

        # Force stable stdio behavior for JSON-over-pipes IPC.
        # (ставим ЖЁСТКО, а не setdefault)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        # Не наследуем parent PYTHONPATH по умолчанию (детерминизм > удобство).
        # Если понадобится совместимость — можно добавить os.environ.get("PYTHONPATH") третьим элементом.
        env["PYTHONPATH"] = os.pathsep.join([str(core_root), str(plugin_root)])

        creationflags = 0
        if os.name == "nt":
            # Avoid opening a new console window for worker
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        dbg = env.get("INVARIANT_WORKER_DEBUG")
        fh = env.get("INVARIANT_WORKER_FAULTHANDLER")
        log.info(
            "spawn worker python=%s plugin_root=%s debug=%s faulthandler=%s",
            python_exe,
            plugin_root,
            dbg,
            fh,
        )
        # Optional (без значений, чтобы не светить секреты): логировать только список ключей
        # log.debug("worker env keys: %s", sorted(env.keys()))

        # Binary pipes to avoid TextIO buffering delays on Windows.
        p = subprocess.Popen(
            [str(python_exe), "-m", "invariant.ops.plugin_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            env=env,
            bufsize=0,  # unbuffered pipes; we do our own buffering/decoding
            creationflags=creationflags,
        )
        log.info("worker spawned pid=%s", getattr(p, "pid", None))
        return p

    def _start_stderr_drain(self) -> None:
        p = self._proc
        if p.stderr is None:
            return
        
        def _emit(line: str) -> None:
            # stderr = "human output" from plugin/worker (prints/logs/tracebacks)
            # tune level as you like; INFO is a sane default.
            try:
                log.info("[worker:%s] %s", self._name, line)
            except Exception:
                pass

        def _drain() -> None:
            try:
                # Binary read to avoid TextIO buffering delays.
                raw = p.stderr
                if raw is None:
                    return

                buf = b""
                max_buf = 50 * 1024 * 1024  # 50MB хватит для любого JSON от LLM
                while True:
                    chunk = raw.read(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        s = line.decode("utf-8", errors="replace").rstrip("\r")
                        self._stderr_tail.append(s)
                        _emit(s)

                    # If buffer grows without newlines, keep it bounded (avoid unbounded memory)
                    if len(buf) > max_buf:
                        log.error(f"FATAL: worker {self._name} stdout line exceeded {max_buf} bytes!")
                        # Лучше уронить воркер явно, чем тихо давиться битым JSON
                        self._proc.kill() 
                        break

                if buf:
                    s = buf.decode("utf-8", errors="replace")
                    self._stderr_tail.append(s)
                    _emit(s)
            except Exception:
                return

        t = threading.Thread(target=_drain, name="invariant-worker-stderr", daemon=True)
        t.start()

    def _start_stdout_drain(self) -> None:
        p = self._proc
        if p.stdout is None:
            return

        def _drain() -> None:
            try:
                log.info("stdout drain started (pid=%s)", getattr(p, "pid", None))
                raw = p.stdout
                if raw is None:
                    return

                buf = b""
                max_buf = 2 * 1024 * 1024  # 2MB safety cap for long JSON lines
                while True:
                    chunk = raw.read(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        s = line.decode("utf-8", errors="replace").rstrip("\r")
                        self._stdout_tail.append(s)
                        try:
                            resp = json.loads(s)
                        except Exception:
                            if log.isEnabledFor(logging.DEBUG):
                                log.debug("stdout noise: %r", s)
                            continue
                        if not isinstance(resp, dict):
                            continue
                        rid = resp.get("id")
                        if not rid:
                            continue
                        with self._cv:
                            self._responses[str(rid)] = resp
                            self._cv.notify_all()

                    if len(buf) > max_buf:
                        s = buf.decode("utf-8", errors="replace")
                        self._stdout_tail.append(s)
                        if log.isEnabledFor(logging.DEBUG):
                            log.debug("stdout noise (truncated): %r", s)
                        buf = b""

                if buf:
                    s = buf.decode("utf-8", errors="replace")
                    self._stdout_tail.append(s)
                    if log.isEnabledFor(logging.DEBUG):
                        log.debug("stdout noise (final): %r", s)
            except Exception:
                log.exception("stdout drain crashed (pid=%s)", getattr(p, "pid", None))
                return

        t = threading.Thread(target=_drain, name="invariant-worker-stdout", daemon=True)
        t.start()

    def _stderr_hint(self) -> str:
        if not self._stderr_tail:
            return ""
        tail = "\n".join(list(self._stderr_tail)[-50:])
        return f"\n--- worker stderr tail ---\n{tail}"
    
    def _stdout_hint(self) -> str:
        if not self._stdout_tail:
            return ""
        tail = "\n".join(list(self._stdout_tail)[-50:])
        return f"\n--- worker stdout tail ---\n{tail}"

    def _dead_hint(self) -> str:
        p = self._proc
        code = p.poll()
        if code is None:
            return ""
        return f"\n--- worker exited ---\nreturncode={code}"

    def request(self, msg: dict[str, Any], *, timeout_s: float | None = 30.0) -> dict[str, Any]:
        req_id = msg.get("id")
        if not req_id:
            raise ValueError("worker request requires 'id'")
        req_id = str(req_id)

        # 1. Готовим данные ДО захвата любых блокировок (экономим процессорное время в крит. секции)
        t0 = time.monotonic()
        payload = json.dumps(msg, ensure_ascii=False) + "\n"
        data = payload.encode("utf-8", errors="replace")
        log.info("worker send op=%s id=%s bytes=%s", msg.get("op"), req_id, len(data))

        # 2. Блокируем ТОЛЬКО запись в stdin. 
        # Если 5 потоков вызовут request одновременно, они аккуратно выстроятся в очередь здесь.
        with self._write_lock:
            p = self._proc
            if p.poll() is not None:
                raise RuntimeError(
                    "worker process is not running"
                    + self._dead_hint() + self._stderr_hint() + self._stdout_hint()
                )

            assert p.stdin is not None
            
            try:
                p.stdin.write(data)
                p.stdin.flush()
            except Exception as e:
                log.exception("worker send failed op=%s id=%s err=%s", msg.get("op"), req_id, e)
                raise

        # 3. Вычисляем дедлайн для Condition Variable
        deadline: float | None = None
        if timeout_s is not None:
            try:
                tv = float(timeout_s)
            except Exception:
                tv = 0.0
            if tv > 0.0:
                deadline = time.monotonic() + tv

        # 4. ОЖИДАНИЕ ОТВЕТА (Без блокировки других запросов!)
        # self._cv работает независимо от _write_lock. Другие потоки могут писать в stdin, пока мы спим здесь.
        with self._cv:
            while True:
                if p.poll() is not None:
                    raise RuntimeError(
                        "worker exited while waiting for response"
                        + self._dead_hint() + self._stderr_hint() + self._stdout_hint()
                    )

                # Проверяем, не пришел ли НАШ ответ (положил поток чтения stdout)
                resp = self._responses.pop(req_id, None)
                if resp is not None:
                    dt_ms = int((time.monotonic() - t0) * 1000)
                    if dt_ms >= 1000:
                        log.info("worker response op=%s id=%s dt_ms=%s", msg.get("op"), req_id, dt_ms)
                    return resp

                # Проверка таймаута
                if deadline is not None:
                    now = time.monotonic()
                    if now > deadline:
                        raise RuntimeError(
                            f"worker request timeout (id={req_id}, op={msg.get('op')!r})"
                            + self._stderr_hint() + self._stdout_hint()
                        )
                    # Спим не более 0.1 сек, чтобы быстро реагировать на смерть процесса
                    self._cv.wait(timeout=min(0.1, max(0.0, deadline - now)))
                else:
                    self._cv.wait(timeout=0.1)

    def close(self, *, graceful: bool = True) -> None:
        # Avoid deadlock: do not call request() while holding the same lock.
        # Проверяем и меняем стейт безопасно
        with self._state_lock:
            if self._closed:
                return
            self._closed = True

        p = self._proc
        if p.poll() is not None:
            return

        # Ask nicely (outside lock to avoid re-entrancy).
        # graceful запрос пойдет через стандартный request, 
        # который теперь безопасно возьмет _write_lock
        if graceful:
            try:
                self.request({"op": "shutdown", "id": str(uuid.uuid4())}, timeout_s=0.2)
            except Exception:
                pass

        # Then terminate, then kill if needed.
        # Жесткое убийство
        try:
            p.terminate()
        except Exception:
            pass
        try:
            p.wait(timeout=0.2 if not graceful else 1.0)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

def _is_worker_timeout_error(err: BaseException) -> bool:
    # Our client raises: "worker request timeout (id=..., op=...)"
    try:
        s = str(err).lower()
    except Exception:
        return False
    return "worker request timeout" in s


def _format_worker_error(resp: dict[str, Any]) -> str:
    err = resp.get("error") or "worker error"
    tb = resp.get("traceback")
    if tb:
        return f"{err}\n{tb}"
    return str(err)


class IsolatedPluginProxy(BasePlugin):
    """
    Плагин-прокси: выполняет методы в отдельном процессе (отдельный venv).
    Совместим с твоим Kernel:
      - initialize(**deps) без *args
      - shutdown() присутствует

    Timeout model:
      - default call timeout is configurable via source.extra:
          * "call_timeout_s": number
          * "method_timeouts": { "method_name": number, ... }
      - explicit timeout_s in call() overrides everything.
      - on timeout: worker is considered tainted => we close it (best-effort) and raise.
    """

    def __init__(self, *, descriptor: PluginDescriptor, env_manager: EnvManager) -> None:
        # Kernel will inject meta/context again, but keep sane defaults here too.
        self.meta = descriptor.meta
        self.context = None

        extra = (descriptor.source.extra or {})
        plugin_root_s = extra.get("plugin_root")
        code_rel = extra.get("code_rel")
        deps_raw = extra.get("deps")

        if not isinstance(plugin_root_s, str) or not plugin_root_s:
            raise PluginLoadError(
                "Isolated plugin requires source.extra['plugin_root']",
                meta_id=descriptor.meta.id,
            )
        if not isinstance(code_rel, str) or not code_rel:
            raise PluginLoadError(
                "Isolated plugin requires source.extra['code_rel']",
                meta_id=descriptor.meta.id,
            )
        if not isinstance(deps_raw, dict):
            raise PluginLoadError(
                "Isolated plugin requires source.extra['deps']",
                meta_id=descriptor.meta.id,
            )

        deps = PluginDepsSpec.from_manifest(deps_raw)
        if not deps.is_isolated():
            raise PluginLoadError(
                "IsolatedPluginProxy created for non-isolated deps",
                meta_id=descriptor.meta.id,
            )
        
        # ---- timeouts (future-proof) ----
        self._default_call_timeout_s: float | None = 60.0
        try:
            v = extra.get("call_timeout_s", 60.0)
            if v is None:
                self._default_call_timeout_s = None
            else:
                self._default_call_timeout_s = float(v)
                if self._default_call_timeout_s <= 0:
                    self._default_call_timeout_s = None
        except Exception:
            self._default_call_timeout_s = 60.0

        self._method_timeouts: dict[str, float | None] = {}
        mt = extra.get("method_timeouts")
        # extra is MappingProxyType; accept any Mapping
        from collections.abc import Mapping as _Mapping  # local to avoid global import
        if isinstance(mt, _Mapping):
            for k, v in mt.items():
                if not isinstance(k, str) or not k.strip():
                    continue
                try:
                    if v is None:
                        self._method_timeouts[k] = None
                    else:
                        fv = float(v)
                        self._method_timeouts[k] = (fv if fv > 0 else None)
                except Exception:
                    continue

        self._closed = False
        self._loaded = False

        plugin_root = Path(plugin_root_s).resolve()
        self._plugin_root = plugin_root
        plugin_key = f"{descriptor.meta.id}@{descriptor.meta.version or 'dev'}"
        log.info("isolated init plugin=%s root=%s mode=%s", plugin_key, plugin_root, deps.mode)
        env_info = env_manager.ensure_env(plugin_key=plugin_key, plugin_root=plugin_root, deps=deps)
        log.info("isolated env ready plugin=%s python=%s", plugin_key, env_info.python_exe)
        self._env_manager = env_manager
        self._env_dir = getattr(env_info, "env_dir", None) or plugin_root

        name = f"{descriptor.meta.id}@{descriptor.meta.version or 'dev'}"
        self._client = _WorkerClient(python_exe=env_info.python_exe, plugin_root=plugin_root, name=name)

        code_path = (plugin_root / code_rel).resolve()
        try:
            code_path.relative_to(plugin_root)
        except Exception:
            raise PluginLoadError(
                "Isolated plugin code must be under plugin_root",
                meta_id=descriptor.meta.id
            ) from None

        entry = {
            "plugin_root": str(plugin_root),
            "file": str(code_path),  # absolute, safer
            "module": descriptor.source.module,
            "class_name": descriptor.source.class_name,
            # Optional payload: worker may ignore, safe to send
            "meta": {
                "id": getattr(descriptor.meta, "id", None),
                "version": getattr(descriptor.meta, "version", None),
                "name": getattr(descriptor.meta, "name", None),
                "description": getattr(descriptor.meta, "description", None),
                "author": getattr(descriptor.meta, "author", None),
                "repo": getattr(descriptor.meta, "repo", None),
                "maturity": getattr(descriptor.meta, "maturity", None),
            },
        }

        try:
            log.info("worker load start plugin=%s", plugin_key)
            resp = self._client.request({"op": "load", "id": str(uuid.uuid4()), "entry": entry}, timeout_s=30.0)
            if not resp.get("ok"):
                raise PluginLoadError(
                    f"Worker failed to load plugin: {resp.get('error')}\n{resp.get('traceback','')}".rstrip(),
                    meta_id=descriptor.meta.id,
                )
            self._loaded = True
            try:
                if hasattr(self._env_manager, "touch_env"):
                    self._env_manager.touch_env(self._env_dir)
            except Exception:
                pass
            log.info("worker load ok plugin=%s", plugin_key)
        except Exception:
            # если load упал — не оставляем воркер висеть
            try:
                self._client.close()
            except Exception:
                pass
            raise

    def _timeout_for(self, method: str, explicit: float | None) -> float | None:
        if explicit is not None:
            try:
                tv = float(explicit)
            except Exception:
                return None
            return tv if tv > 0 else None
        if method in self._method_timeouts:
            return self._method_timeouts[method]
        return self._default_call_timeout_s
    
    def _call_impl(self, method: str, args: list[Any], kwargs: dict[str, Any], *, timeout_s: float | None) -> Any:
        msg = {
            "op": "call",
            "id": str(uuid.uuid4()),
            "method": method,
            "args": list(args),
            "kwargs": dict(kwargs),
        }
        try:
            if hasattr(self._env_manager, "touch_env"):
                self._env_manager.touch_env(self._env_dir)
        except Exception:
            pass
        try:
            encoded_msg = assert_rpc_json_serializable(msg)
        except Exception as e:
            raise TypeError(
                f"isolated plugin call payload must be JSON-serializable "
                f"(method={method!r}): {type(e).__name__}: {e}"
            ) from e

        try:
            if timeout_s is not None and timeout_s > 0:
                fut = _REQUEST_POOL.submit(self._client.request, encoded_msg, timeout_s=timeout_s)
                try:
                    resp = fut.result(timeout=float(timeout_s))
                except cf.TimeoutError as e:
                    try:
                        self._client.close(graceful=False)
                    except Exception:
                        pass
                    raise RuntimeError(f"isolated plugin call timeout: {method} (timeout_s={timeout_s})") from e
            else:
                resp = self._client.request(encoded_msg, timeout_s=timeout_s)
        except RuntimeError as e:
            if _is_worker_timeout_error(e):
                try:
                    self._client.close(graceful=False)
                except Exception:
                    pass
                raise RuntimeError(f"isolated plugin call timeout: {method} (timeout_s={timeout_s})") from e
            raise

        if not resp.get("ok"):
            raise RuntimeError(_format_worker_error(resp))
        return decode_rpc_value(resp.get("result"))

    def call(self, method: str, *args: Any, timeout_s: float | None = None, **kwargs: Any) -> Any:
        """
        Explicit worker call with per-call timeout override.
        Safer than relying on __getattr__ magic when you need control.
        """
        if not self._loaded:
            raise RuntimeError("plugin not loaded in worker")
        if not isinstance(method, str) or not method.strip():
            raise TypeError("method must be non-empty str")

        eff = self._timeout_for(method, timeout_s)
        return self._call_impl(method, list(args), dict(kwargs), timeout_s=eff)

    def initialize(self, **deps: object) -> None:
        # Contract: isolated worker boundary accepts ONLY JSON-safe transport payloads.
        # Object deps (capabilities/plugins/contexts) are still not allowed.
        if deps:
            bad: list[str] = []
            for k, v in deps.items():
                try:
                    assert_rpc_json_serializable(v)
                except Exception:
                    bad.append(str(k))
            if bad:
                raise RuntimeError(
                    "isolated plugin cannot receive non-JSON dependency objects via initialize(); "
                    f"non-serializable deps: {bad}. "
                    "Pass config/params only, or implement RPC handles for deps."
                )
        try:
            self.call("initialize", timeout_s=self._timeout_for("initialize", None), **deps)
        except RuntimeError as e:
            # If plugin doesn't define initialize() and we have no deps -> allow.
            if not deps:
                s = str(e).lower()
                if ("method not found" in s) or ("attributeerror" in s) or ("has no attribute" in s):
                    return
            raise

    def shutdown(self) -> None:
        # для Kernel.shutdown()/shutdown_force()
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        # best-effort: дать плагину в воркере шанс корректно завершиться
        if self._loaded:
            try:
                _ = self._client.request(
                    {"op": "call", "id": str(uuid.uuid4()), "method": "shutdown", "args": [], "kwargs": {}},
                    timeout_s=1.0,
                )
            except Exception:
                pass

        try:
            self._client.close()
        except Exception:
            pass

    def __getattr__(self, name: str):
        # Прокидываем только "пользовательские" методы.
        if name.startswith("_"):
            raise AttributeError(name)

        def _call(*args, **kwargs):
            # IMPORTANT:
            # Do NOT pass "timeout_s=..." into call() here, because plugin methods may legitimately
            # have an argument named "timeout_s". That would cause a TypeError (duplicate kw).
            #
            # We reserve "_timeout_s" for transport timeout override.
            override = kwargs.pop("_timeout_s", None) if "_timeout_s" in kwargs else None
            eff = self._timeout_for(name, override)
            return self._call_impl(name, list(args), dict(kwargs), timeout_s=eff)

        return _call

    def __del__(self) -> None:
        # Avoid leaving zombie workers in tests/dev.
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self) -> IsolatedPluginProxy:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @staticmethod
    def from_descriptor(
        descriptor: PluginDescriptor,
        *,
        env_manager: EnvManager,
    ) -> IsolatedPluginProxy:
        """
        Compatibility helper for PluginLoader.load().
        """
        return IsolatedPluginProxy(descriptor=descriptor, env_manager=env_manager)
