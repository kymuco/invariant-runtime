# invariant/ops/supervisor.py
from __future__ import annotations

import concurrent.futures
from contextlib import suppress
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, Optional

from invariant.api.service import PluginService
from invariant.exec.runtime.execution_context import ExecutionContext

from invariant.ops.trace_context import bind_execution
from invariant.ops.errors import (
    ExecutionNotFound,
    ExecutionStateError,
    PluginHandleNotFound,
    PluginCallError,
    ExecutionLimitExceeded,
    PluginCallTimeout,
)
from invariant.ops.metrics import RuntimeMetrics


class ExecutionState(Enum):
    CREATED = auto()
    ACTIVE = auto()
    COMMITTED = auto()
    ROLLED_BACK = auto()
    CLOSED = auto()
    FAILED = auto()


class CloseReason(Enum):
    USER_COMMIT = auto()
    USER_ROLLBACK = auto()
    TTL_EXPIRED = auto()
    FAILED = auto()
    LIMIT_ENFORCED = auto()


@dataclass
class ExecutionSession:
    execution_id: str
    ctx: ExecutionContext
    state: ExecutionState = ExecutionState.CREATED

    # Use monotonic clock for TTL/age computations (time.time can jump).
    created_at: float = field(default_factory=lambda: time.monotonic())
    last_used_at: float = field(default_factory=lambda: time.monotonic())
    ttl_seconds: int = 300

    close_reason: Optional[CloseReason] = None
    close_detail: Optional[str] = None

    _handles: Dict[int, Any] = field(default_factory=dict)
    _next_handle: int = 1
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _call_gate: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _inflight_calls: int = 0
    _closing: bool = False
    _finalized: bool = False

    def touch(self) -> None:
        self.last_used_at = time.monotonic()

    def is_expired(self) -> bool:
        # If a call is in-flight, treat as "not idle" (avoid expiring mid-call).
        if self._inflight_calls > 0:
            return False
        return (time.monotonic() - self.last_used_at) > self.ttl_seconds

    def open(self) -> None:
        if self.state is not ExecutionState.CREATED:
            raise ExecutionStateError(f"Cannot open execution in state {self.state.name}")
        # self.ctx.__enter__()
        self.state = ExecutionState.ACTIVE
        self.touch()

    def close(self, *, reason: CloseReason, detail: str | None = None) -> None:
        if self.state is ExecutionState.CLOSED:
            return
        self.close_reason = reason
        self.close_detail = detail
        try:
            self.ctx.close()
        finally:
            self.state = ExecutionState.CLOSED
            self._handles.clear()

    def commit(self) -> None:
        if self.state is not ExecutionState.ACTIVE:
            raise ExecutionStateError(f"Cannot commit execution in state {self.state.name}")
        self.state = ExecutionState.COMMITTED
        self.close(reason=CloseReason.USER_COMMIT)

    def rollback(self, *, detail: str | None = None) -> None:
        if self.state is not ExecutionState.ACTIVE:
            raise ExecutionStateError(f"Cannot rollback execution in state {self.state.name}")
        self.state = ExecutionState.ROLLED_BACK
        self.close(reason=CloseReason.USER_ROLLBACK, detail=detail)

    def fail_and_close(self, *, detail: str | None = None) -> None:
        self.state = ExecutionState.FAILED
        self.close(reason=CloseReason.FAILED, detail=detail)

    def _new_handle(self, obj: Any) -> int:
        h = self._next_handle
        self._next_handle += 1
        self._handles[h] = obj
        return h

    def _get_handle(self, handle: int) -> Any:
        try:
            return self._handles[handle]
        except KeyError:
            raise PluginHandleNotFound(f"Unknown handle: {handle}")


class ExecutionSupervisor:
    """
    Manages lifecycle of ExecutionSession objects.
    """
    def __init__(
        self,
        service: PluginService,
        *,
        max_active_executions: int = 64,
        default_ttl_seconds: int = 300,
        cleanup_interval_seconds: int = 5,
    ):
        self._service = service
        self._executions: Dict[str, ExecutionSession] = {}
        self._max_active = int(max_active_executions)
        self._default_ttl = int(default_ttl_seconds)

        self._cleanup_interval = max(1, int(cleanup_interval_seconds))
        self._next_cleanup_at = time.monotonic() + self._cleanup_interval

        self._lock = threading.RLock()
        self._timeout_pool: concurrent.futures.ThreadPoolExecutor | None = None

    def close(self) -> None:
        p = self._timeout_pool
        self._timeout_pool = None
        if p is not None:
            try:
                p.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                p.shutdown(wait=False)
            except Exception:
                pass

    def _get_timeout_pool(self) -> concurrent.futures.ThreadPoolExecutor:
        p = self._timeout_pool
        if p is not None:
            return p
        # Only used for timeout-enabled calls. Keep it bounded.
        cpu = os.cpu_count() or 4
        workers = max(4, min(32, cpu * 4))
        self._timeout_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="invariant-timeout",
        )
        return self._timeout_pool

    # ---------------- internal helpers ----------------

    def _metrics(self) -> Optional[RuntimeMetrics]:
        tracer = getattr(self._service, "tracer", None)
        return getattr(tracer, "metrics", None)

    def _tracer(self):
        return getattr(self._service, "tracer", None)

    def _close_execution(
        self,
        *,
        execution_id: str,
        reason: CloseReason,
        detail: str | None = None,
        keep_in_table: bool = False,
        expected_state: ExecutionState | None = None,
        was_active_hint: bool | None = None,
    ) -> None:
        """
        Single source of truth for execution closure.

        - closes context
        - updates metrics
        - emits tracer close event
        - removes from table unless keep_in_table=True
        """
        tracer = self._tracer()
        metrics = self._metrics()

        # Phase 1: detach from table + update metrics under global lock
        with self._lock:
            session = self._executions.get(execution_id)
            if not session:
                return

            if session.state is ExecutionState.CLOSED:
                if not keep_in_table:
                    self._executions.pop(execution_id, None)
                return

            if expected_state is not None and session.state is not expected_state:
                raise ExecutionStateError(
                    f"Cannot close execution in state {session.state.name} (expected {expected_state.name})"
                )

            was_active = was_active_hint if was_active_hint is not None else (session.state is ExecutionState.ACTIVE)

            # Remove from table early so nobody can "get()" it again while it's closing
            if not keep_in_table:
                self._executions.pop(execution_id, None)

            # Metrics (best-effort; never raises)
            if metrics:
                try:
                    if was_active:
                        metrics.executions_active = max(0, metrics.executions_active - 1)
                    metrics.executions_closed_total.inc(reason.name, 1)
                    if reason is CloseReason.TTL_EXPIRED:
                        metrics.executions_expired_total.inc(1)
                    if reason is CloseReason.FAILED:
                        metrics.executions_failed_total.inc(1)
                except Exception:
                    pass

        # Phase 2: close context outside global lock (may be slow)
        should_finalize_now = False
        with session._lock:
            # Mark as closing and detach handles immediately
            session._closing = True
            session.close_reason = reason
            session.close_detail = detail
            session.state = ExecutionState.CLOSED
            session._handles.clear()

            # If there are inflight calls, defer ctx.close() to the last finishing call.
            if not session._finalized and session._inflight_calls <= 0:
                session._finalized = True
                should_finalize_now = True

        if should_finalize_now:
            try:
                session.ctx.close()
            except Exception:
                pass

        # Tracing (best-effort)
        if tracer and hasattr(tracer, "note_execution_closed"):
            try:
                tracer.note_execution_closed(
                    execution_id=execution_id,
                    reason=reason.name,
                    detail=detail,
                )
            except Exception:
                pass

    # ---------- periodic hook ----------

    def tick(self) -> int:
        """
        Periodic maintenance hook.
        Call this from the host loop (or after each command).
        Returns number of cleaned executions.
        """
        now = time.monotonic()
        with self._lock:
            if now < self._next_cleanup_at:
                return 0
            self._next_cleanup_at = now + self._cleanup_interval
        # cleanup outside global lock
        return self.cleanup_expired()

    # ---------- maintenance ----------

    def cleanup_expired(self) -> int:
        with self._lock:
            expired_ids = [
                eid
                for eid, s in self._executions.items()
                if s.state is ExecutionState.ACTIVE and s.is_expired()
            ]

        for eid in expired_ids:
            msg = f"Execution expired by TTL. Auto-rollback."
            tracer = self._tracer()
            if tracer and hasattr(tracer, "note_runtime_warning"):
                try:
                    tracer.note_runtime_warning(execution_id=eid, message=msg)
                except Exception:
                    pass

            self._close_execution(execution_id=eid, reason=CloseReason.TTL_EXPIRED, detail=msg)

        return len(expired_ids)

    def _enforce_limits(self) -> None:
        active = sum(1 for s in self._executions.values() if s.state is ExecutionState.ACTIVE)
        if active >= self._max_active:
            metrics = self._metrics()
            if metrics:
                try:
                    metrics.execution_limit_exceeded_total.inc(1)
                except Exception:
                    pass
            raise ExecutionLimitExceeded(f"Too many active executions: {active} (limit={self._max_active})")

    # ---------- execution lifecycle ----------

    def create(
        self,
        *,
        policy,
        dependency_overrides=None,
        ttl_seconds: Optional[int] = None,
    ) -> str:
        # cleanup may close contexts; do it outside global lock
        self.cleanup_expired()
        
        with self._lock:
            self._enforce_limits()
            execution_id = str(uuid.uuid4())

        ctx = self._service.execution(
            policy=policy,
            dependency_overrides=dependency_overrides or {},
        )

        session = ExecutionSession(
            execution_id=execution_id,
            ctx=ctx,
            ttl_seconds=max(1, int(ttl_seconds)) if ttl_seconds is not None else self._default_ttl,
        )
        session.open()

        try:
            with self._lock:
                # Re-check limits right before insert (race-safe).
                self._enforce_limits()
                self._executions[execution_id] = session

        except Exception:
            # Context must not leak if we fail to insert.
            with suppress(Exception):
                ctx.close()
            raise

        metrics = self._metrics()
        if metrics:
            try:
                metrics.executions_created_total.inc(1)
                metrics.executions_active += 1
            except Exception:
                pass

        return execution_id

    def get(self, execution_id: str) -> ExecutionSession:
        msg: str | None = None
        with self._lock:
            session = self._executions.get(execution_id)
            if not session:
                raise ExecutionNotFound(execution_id)

            if session.state is ExecutionState.ACTIVE and session.is_expired():
                msg = f"Execution expired by TTL ({session.ttl_seconds}s idle) on access. Auto-rollback."
                tracer = self._tracer()
                if tracer and hasattr(tracer, "note_runtime_warning"):
                    try:
                        tracer.note_runtime_warning(execution_id=execution_id, message=msg)
                    except Exception:
                        pass

            else:
                return session

        # expired on access: close outside global lock
        assert msg is not None
        self._close_execution(
            execution_id=execution_id,
            reason=CloseReason.TTL_EXPIRED,
            detail=msg,
        )
        raise ExecutionNotFound(f"{execution_id} (expired)")

    def commit(self, execution_id: str) -> None:
        # state validation happens inside _close_execution under global lock
        self._close_execution(
            execution_id=execution_id,
            reason=CloseReason.USER_COMMIT,
            expected_state=ExecutionState.ACTIVE,
        )

    def rollback(self, execution_id: str, *, detail: str | None = None) -> None:
        self._close_execution(
            execution_id=execution_id,
            reason=CloseReason.USER_ROLLBACK,
            detail=detail,
            expected_state=ExecutionState.ACTIVE,
        )

    def list_executions(self) -> list[dict]:
        with self._lock:
            out = []
            now = time.monotonic()
            for eid, s in self._executions.items():
                out.append(
                    {
                        "execution": eid,
                        "state": s.state.name,
                        "age_s": round(now - s.created_at, 3),
                        "idle_s": round(now - s.last_used_at, 3),
                        "ttl_s": s.ttl_seconds,
                        "close_reason": s.close_reason.name if s.close_reason else None,
                        "close_detail": s.close_detail,
                    }
                )
            return out

    # ---------- plugin access / calls ----------

    def plugin_get(self, *, execution_id: str, plugin_id: str) -> int:
        session = self.get(execution_id)
        tracer = self._tracer()

        # Phase A: cheap checks under lock (do NOT hold lock during activation)
        with session._lock:
            if session.state is not ExecutionState.ACTIVE or session._closing:
                raise ExecutionStateError(f"Execution is not active: {session.state.name}")

            if tracer and hasattr(tracer, "note_execution_touched"):
                try:
                    tracer.note_execution_touched(
                        execution_id=execution_id,
                        where="plugin_get:pre",
                        detail=f"plugin_id={plugin_id}",
                    )
                except Exception:
                    pass

            session.touch()

            # Phase B: activation outside session lock
        with bind_execution(execution_id):
            plugin = session.ctx.get(plugin_id)

        # Phase C: store handle under lock
        with session._lock:
            if session.state is not ExecutionState.ACTIVE or session._closing:
                raise ExecutionStateError("Execution closed while getting plugin")
            session.touch()
            if tracer and hasattr(tracer, "note_execution_touched"):
                try:
                    tracer.note_execution_touched(
                        execution_id=execution_id,
                        where="plugin_get:post",
                        detail=f"plugin_id={plugin_id}",
                    )
                except Exception:
                    pass
            return session._new_handle(plugin)

    def plugin_call(
        self,
        *,
        execution_id: str,
        handle: int,
        method: str,
        args: list | None = None,
        kwargs: dict | None = None,
        timeout_s: float | None = None,
    ) -> Any:
        """
        Call a plugin method within an execution.

        Guarantees:
        - Execution TTL is extended on use.
        - If the call raises, execution is closed with FAILED reason (containment).
        - Metrics are recorded for duration + errors.
        - Tracing is bound to execution_id via contextvars.
        """
        session = self.get(execution_id)
        tracer = self._tracer()
        metrics = self._metrics()

        # Phase A: resolve fn + bookkeep under lock (never run user code under this lock)
        with session._lock:
            if session.state is not ExecutionState.ACTIVE or session._closing:
                raise ExecutionStateError(f"Execution is not active: {session.state.name}")

            # Touch (keep alive) + optional debug trace
            if tracer and hasattr(tracer, "note_execution_touched"):
                try:
                    tracer.note_execution_touched(
                        execution_id=execution_id,
                        where="plugin_call:pre",
                        detail=f"handle={handle} method={method}",
                    )
                except Exception:
                    pass

            session.touch()

            # Resolve handle -> plugin object
            plugin = session._get_handle(handle)

            # Resolve method
            fn = getattr(plugin, method, None)
            if fn is None or not callable(fn):
                raise PluginCallError(f"Plugin has no callable method '{method}'")

            # Build label for metrics: "<plugin_id>.<method>"
            pid = None
            try:
                fsm = getattr(plugin, "fsm", None)
                pid = getattr(fsm, "plugin_id", None)
            except Exception:
                pid = None
            call_label = f"{pid}.{method}" if pid else f"handle:{handle}.{method}"
            session._inflight_calls += 1

        # Helpers: decrement inflight + finalize deferred close if needed
        def _after_call() -> None:
            finalize = False
            with session._lock:
                session._inflight_calls = max(0, session._inflight_calls - 1)
                if session._closing and not session._finalized and session._inflight_calls <= 0:
                    session._finalized = True
                    finalize = True
            if finalize:
                try:
                    session.ctx.close()
                except Exception:
                    pass

        # Normalize timeout once; avoid recursion.
        effective_timeout: float | None = None
        if timeout_s is not None:
            try:
                tv = float(timeout_s)
            except Exception:
                tv = 0.0
            if tv > 0.0:
                effective_timeout = tv

        # --- No-timeout path: run inline (but serialized by _call_gate) ---
        if effective_timeout is None:
            t0 = time.time()
            try:
                with session._call_gate:
                    with bind_execution(execution_id):
                        result = fn(*(args or []), **(kwargs or {}))
                dt = time.time() - t0
                if metrics:
                    try:
                        metrics.plugin_call_duration_seconds.observe(call_label, dt)
                    except Exception:
                        pass
                with session._lock:
                    session.touch()
                if tracer and hasattr(tracer, "note_execution_touched"):
                    try:
                        tracer.note_execution_touched(
                            execution_id=execution_id,
                            where="plugin_call:post",
                            detail=f"handle={handle} method={method} dt={dt:.6f}s",
                        )
                    except Exception:
                        pass
                return result
            except Exception as e:
                dt = time.time() - t0
                detail = f"{type(e).__name__}: {e}"
                if metrics:
                    try:
                        metrics.plugin_call_duration_seconds.observe(call_label, dt)
                        metrics.plugin_call_errors_total.inc(call_label, 1)
                    except Exception:
                        pass
                self._close_execution(
                    execution_id=execution_id,
                    reason=CloseReason.FAILED,
                    detail=detail,
                    was_active_hint=True,
                )
                raise PluginCallError(str(e)) from e
            finally:
                _after_call()

        # --- Timeout path: run in bounded pool ---
        timeout_v = effective_timeout

        pool = self._get_timeout_pool()
        t0 = time.time()

        def _run() -> Any:
            try:
                with session._call_gate:
                    # If execution was closed while we were queued, skip user code.
                    with session._lock:
                        if session._closing or session.state is not ExecutionState.ACTIVE:
                            return None
                    with bind_execution(execution_id):
                        return fn(*(args or []), **(kwargs or {}))
            finally:
                dt = time.time() - t0
                if metrics:
                    try:
                        metrics.plugin_call_duration_seconds.observe(call_label, dt)
                    except Exception:
                        pass
                _after_call()

        fut = pool.submit(_run)
        try:
            return fut.result(timeout=float(timeout_v))
        except concurrent.futures.TimeoutError:
            detail = f"plugin_call timeout: {call_label} timeout_s={timeout_v}"
            # IMPORTANT: close must be non-blocking while call continues in background thread
            self._close_execution(
                execution_id=execution_id,
                reason=CloseReason.LIMIT_ENFORCED,
                detail=detail,
                was_active_hint=True,
            )
            raise PluginCallTimeout("timeout", detail=detail)
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            if metrics:
                try:
                    metrics.plugin_call_errors_total.inc(call_label, 1)
                except Exception:
                    pass
            self._close_execution(
                execution_id=execution_id,
                reason=CloseReason.FAILED,
                detail=detail,
                was_active_hint=True,
            )
            raise PluginCallError(str(e)) from e