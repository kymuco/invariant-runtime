# invariant/load/env/env_manager.py
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import time
import venv
from dataclasses import dataclass
from pathlib import Path

from invariant.load.env.lock import FileLock
from invariant.load.env.pip_ops import pip_download_wheelhouse, pip_install_wheelhouse
from invariant.spec.deps import PluginDepsSpec


def _default_home() -> Path:
    # Consistent with your sandbox usage: keep under project cwd by default.
    # Can be overridden for daemon/production usage.
    env = os.environ.get("INVARIANT_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.cwd() / ".invariant").resolve()

log = logging.getLogger("invariant.env")


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()[:16]


def _plat_tag() -> str:
    # Lightweight tag; good enough for cache key.
    return f"{sys.platform}_{os.uname().machine if hasattr(os, 'uname') else 'unknown'}".lower()


@dataclass(frozen=True)
class EnvInfo:
    env_dir: Path
    python_exe: Path
    key: str


@dataclass(frozen=True, slots=True)
class _GcEnvInfo:
    env_dir: Path
    last_used: float
    size_b: int


class EnvManager:
    def __init__(self, *, home: Path | None = None) -> None:
        self.home = home or _default_home()
        self.envs_dir = self.home / "envs"
        self.wheels_dir = self.home / "wheelhouse"
        self.locks_dir = self.home / "locks"
        self.envs_dir.mkdir(parents=True, exist_ok=True)
        self.wheels_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir.mkdir(parents=True, exist_ok=True)
        self._gc_last_at = 0.0
        try:
            self._gc_ttl_s = float(os.environ.get("INVARIANT_ENV_GC_TTL_S", str(20 * 60)))
        except Exception:
            self._gc_ttl_s = 20 * 60.0
        try:
            self._gc_interval_s = float(os.environ.get("INVARIANT_ENV_GC_INTERVAL_S", str(60)))
        except Exception:
            self._gc_interval_s = 60.0
        try:
            self._gc_max_gb = float(os.environ.get("INVARIANT_ENV_GC_MAX_GB", "3"))
        except Exception:
            self._gc_max_gb = 3.0
        if self._gc_max_gb <= 0:
            self._gc_max_bytes = 0
        else:
            self._gc_max_bytes = int(self._gc_max_gb * (1024 ** 3))

    def _python_in_env(self, env_dir: Path) -> Path:
        if sys.platform.startswith("win"):
            return env_dir / "Scripts" / "python.exe"
        return env_dir / "bin" / "python"
    
    def _touch_env(self, env_dir: Path) -> None:
        """
        Update last-used marker for GC. Best-effort only.
        """
        try:
            marker = env_dir / ".invariant_env_last_used"
            marker.write_text(str(time.time()), encoding="utf-8")
        except Exception:
            # Best-effort: do not break env usage if marker can't be written.
            pass

    def touch_env(self, env_dir: Path) -> None:
        self._touch_env(env_dir)

    def _env_last_used(self, env_dir: Path) -> float:
        marker = env_dir / ".invariant_env_last_used"
        ready = env_dir / ".invariant_env_ready"
        ts = 0.0
        try:
            if marker.exists():
                try:
                    ts = max(ts, float(marker.read_text(encoding="utf-8").strip() or "0"))
                except Exception:
                    ts = max(ts, marker.stat().st_mtime)
        except Exception:
            pass
        try:
            if ready.exists():
                ts = max(ts, ready.stat().st_mtime)
        except Exception:
            pass
        if ts <= 0:
            try:
                ts = env_dir.stat().st_mtime
            except Exception:
                ts = time.time()
        return ts

    def _dir_size(self, root: Path) -> int:
        total = 0
        try:
            for base, _dirs, files in os.walk(root):
                for name in files:
                    try:
                        total += (Path(base) / name).stat().st_size
                    except Exception:
                        continue
        except Exception:
            return total
        return total

    def gc_envs(self, *, ttl_s: float | None = None, max_remove: int = 64) -> int:
        """
        Garbage-collect old isolated environments.
        """
        now = time.time()
        ttl = self._gc_ttl_s if ttl_s is None else float(ttl_s)
        max_bytes = self._gc_max_bytes
        if ttl <= 0 and max_bytes <= 0:
            return 0
        if self._gc_interval_s > 0 and (now - self._gc_last_at) < self._gc_interval_s:
            return 0
        self._gc_last_at = now

        removed = 0
        try:
            entries = list(self.envs_dir.iterdir())
        except Exception:
            return 0

        infos: list[_GcEnvInfo] = []
        total_size = 0
        for env_dir in entries:
            if not env_dir.is_dir():
                continue
            # if removed >= max_remove:
            #     break

            last_used = self._env_last_used(env_dir)
            size_b = self._dir_size(env_dir)
            total_size += size_b
            infos.append(_GcEnvInfo(
                env_dir=env_dir,
                last_used=last_used,
                size_b=size_b
            ))

        # Sort oldest-first.
        infos.sort(key=lambda x: x.last_used)

        def _maybe_remove(info: _GcEnvInfo, *, reason: str) -> bool:
            nonlocal removed, total_size
            env_dir = info.env_dir
            if removed >= max_remove:
                return False
            
            lock = FileLock(self.locks_dir / f"{env_dir.name}.lock")
            try:
                lock.acquire(timeout_s=0.0, poll_s=0.0)
            except TimeoutError:
                return False
            except Exception:
                return False

            try:
                # Re-check after lock (race-safe)
                last_used = self._env_last_used(env_dir)
                age_s = now - last_used

                if reason == "ttl" and ttl > 0 and age_s <= ttl:
                    return False
                if reason == "size" and max_bytes > 0 and total_size <= max_bytes:
                    return False
                
                size_b = self._dir_size(env_dir)
                shutil.rmtree(env_dir, ignore_errors=False)
                removed += 1
                total_size = max(0, total_size - size_b)

                log.info(
                    "env gc removed: %s (reason=%s age_s=%.1f size_mb=%.1f)",
                    env_dir,
                    reason,
                    age_s,
                    size_b / (1024 ** 2),
                )
                return True
            except Exception as e:
                log.warning("env gc failed: %s err=%s", env_dir, e)
                return False
            finally:
                try:
                    lock.release()
                except Exception:
                    pass

        # Pass 1: TTL-based cleanup.
        if ttl > 0:
            for info in infos:
                if (now - info.last_used) <= ttl:
                    continue
                _maybe_remove(info, reason="ttl")
                if removed >= max_remove:
                    break

        # Pass 2: size pressure cleanup.
        if max_bytes > 0 and total_size > max_bytes and removed < max_remove:
            for info in infos:
                if total_size <= max_bytes or removed >= max_remove:
                    break
                _maybe_remove(info, reason="size")
                
        return removed

    def _venv_key(self, *, plugin_key: str, requirements_path: Path, deps: PluginDepsSpec) -> str:
        rh = _sha256_file(requirements_path)
        py = f"py{sys.version_info.major}{sys.version_info.minor}"
        plat = _plat_tag()
        mode = deps.mode
        cfg = {
            "index_url": deps.index_url,
            "extra_index_urls": list(deps.extra_index_urls),
            "trusted_hosts": list(deps.trusted_hosts),
            "only_binary": bool(deps.only_binary),
            "mode": deps.mode,
        }
        cfg_b = json.dumps(cfg, ensure_ascii=False, sort_keys=True).encode("utf-8")
        cfgh = hashlib.sha256(cfg_b).hexdigest()[:8]
        safe = plugin_key.replace(":", "_").replace("/", "_").replace("\\", "_")
        return f"{safe}__{mode}__{py}__{plat}__{rh}__{cfgh}"

    def ensure_env(self, *, plugin_key: str, plugin_root: Path, deps: PluginDepsSpec) -> EnvInfo:
        if deps.mode == "none":
            raise ValueError("ensure_env called with deps.mode='none'")
        if not deps.requirements:
            raise ValueError("deps.requirements is required")

        try:
            self.gc_envs()
        except Exception:
            pass

        t0 = time.monotonic()
        requirements_path = (plugin_root / deps.requirements).resolve()
        if not requirements_path.exists():
            raise FileNotFoundError(f"requirements not found: {requirements_path}")

        key = self._venv_key(plugin_key=plugin_key, requirements_path=requirements_path, deps=deps)
        env_dir = (self.envs_dir / key).resolve()
        python_exe = self._python_in_env(env_dir)

        log.info(
            "ensure_env start plugin=%s mode=%s env_dir=%s requirements=%s",
            plugin_key,
            deps.mode,
            env_dir,
            requirements_path,
        )

        lock = FileLock(self.locks_dir / f"{key}.lock")
        with lock:
            marker = env_dir / ".invariant_env_ready"
            if marker.exists() and python_exe.exists():
                log.info("ensure_env reuse ready env: %s", env_dir)
                self._touch_env(env_dir)
                return EnvInfo(env_dir=env_dir, python_exe=python_exe, key=key)

            if not python_exe.exists():
                log.info("ensure_env create venv: %s", env_dir)
                env_dir.parent.mkdir(parents=True, exist_ok=True)
                venv.EnvBuilder(with_pip=True, system_site_packages=False, clear=False).create(str(env_dir))
                python_exe = self._python_in_env(env_dir)

            # wheelhouse selection
            if deps.mode == "bundle":
                if not deps.wheels_dir:
                    raise ValueError("deps.wheels_dir is required for bundle mode")
                wheelhouse = (plugin_root / deps.wheels_dir).resolve()
                log.info("ensure_env bundle wheelhouse: %s", wheelhouse)
            else:
                # online -> download into cache wheelhouse
                if os.environ.get("INVARIANT_DISABLE_ONLINE_DEPS") in {"1", "true", "yes"}:
                    raise PermissionError("Online deps disabled (INVARIANT_DISABLE_ONLINE_DEPS=1)")
                wheelhouse = (self.wheels_dir / key).resolve()
                wheelhouse.mkdir(parents=True, exist_ok=True)
                log.info("ensure_env download wheelhouse: %s", wheelhouse)
                pip_download_wheelhouse(
                    python_exe=python_exe,
                    requirements_path=requirements_path,
                    wheelhouse_dir=wheelhouse,
                    index_url=deps.index_url,
                    extra_index_urls=deps.extra_index_urls,
                    trusted_hosts=deps.trusted_hosts,
                    only_binary=deps.only_binary,
                )

            log.info("ensure_env install wheelhouse: %s", wheelhouse)
            pip_install_wheelhouse(
                python_exe=python_exe,
                requirements_path=requirements_path,
                wheelhouse_dir=wheelhouse,
            )

            marker.write_text("ok\n", encoding="utf-8")
            self._touch_env(env_dir)
            dt_ms = int((time.monotonic() - t0) * 1000)
            log.info("ensure_env done env_dir=%s dt_ms=%s", env_dir, dt_ms)
            return EnvInfo(env_dir=env_dir, python_exe=python_exe, key=key)
