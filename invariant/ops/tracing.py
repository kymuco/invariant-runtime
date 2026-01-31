# invariant/ops/tracing.py
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional
import threading
import time

from invariant.exec.runtime.activation import ActivationEvent, ActivationEventType
from invariant.exec.runtime.lifecycle import LifecycleEvent
from invariant.ops.trace_context import get_current_execution_id
from invariant.ops.metrics import RuntimeMetrics


def _safe_serialize(obj: Any) -> Any:
    # dataclass -> dict recursively
    if is_dataclass(obj):
        return _safe_serialize(asdict(obj))

    # Enum -> stable string
    if isinstance(obj, Enum):
        return obj.name

    # Path -> str
    if isinstance(obj, Path):
        return str(obj)

    # primitives
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj

    # containers
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _safe_serialize(v) for k, v in obj.items()}

    # fallback
    return {"repr": repr(obj)}


class InMemoryRuntimeTracer:
    """
    In-memory tracer with bounded memory + thread safety.

    Design goals:
    - safe under concurrent access (watchdog thread + host thread)
    - bounded RAM: LRU eviction for tx/plugin/execution streams
    - structured events (JSON-friendly)
    """

    def __init__(
        self,
        *,
        max_events_per_stream: int = 2000,
        max_tx_streams: int = 512,
        max_plugin_streams: int = 1024,
        max_execution_streams: int = 512,
        max_tx_to_execution: int = 4096,
        recent_tx_size: int = 200,
        debug_touch_events: bool = False,
        metrics: RuntimeMetrics | None = None,
    ) -> None:
        self._max_events = max(10, int(max_events_per_stream))
        self._max_tx_streams = max(10, int(max_tx_streams))
        self._max_plugin_streams = max(10, int(max_plugin_streams))
        self._max_exec_streams = max(10, int(max_execution_streams))
        self._max_tx_to_exec = max(10, int(max_tx_to_execution))
        self._debug_touch = bool(debug_touch_events)

        self.metrics = metrics or RuntimeMetrics()

        # Thread safety
        self._lock = threading.RLock()

        # LRU maps: key -> deque(events)
        self._activation_by_tx: "OrderedDict[str, Deque[ActivationEvent]]" = OrderedDict()
        self._lifecycle_by_plugin: "OrderedDict[str, Deque[LifecycleEvent]]" = OrderedDict()
        self._execution_events: "OrderedDict[str, Deque[dict]]" = OrderedDict()

        # recent tx ids (bounded)
        self._activation_recent: Deque[str] = deque(maxlen=max(10, int(recent_tx_size)))

        # tx -> execution (bounded LRU)
        self._tx_to_execution: "OrderedDict[str, str]" = OrderedDict()

        # tx timing (bounded; cleaned on END)
        self._tx_started_at: Dict[str, float] = {}
        self._tx_root: Dict[str, str] = {}

    # ---------------- internal: LRU helpers ----------------

    def _get_stream(
        self,
        table: "OrderedDict[str, Deque[Any]]",
        key: str,
        *,
        max_streams: int,
    ) -> "Deque[Any]":
        """
        Get/create a stream deque for key and update LRU order.
        Evicts oldest streams if table grows beyond max_streams.
        """
        dq = table.get(key)
        if dq is None:
            dq = deque(maxlen=self._max_events)
            table[key] = dq
        else:
            table.move_to_end(key, last=True)

        while len(table) > max_streams:
            table.popitem(last=False)  # drop oldest stream

        return dq

    def _lru_set(self, table: "OrderedDict[str, str]", key: str, value: str, *, max_items: int) -> None:
        table[key] = value
        table.move_to_end(key, last=True)
        while len(table) > max_items:
            table.popitem(last=False)

    # ---------------- core callbacks ----------------

    def on_activation_event(self, e: ActivationEvent) -> None:
        exec_id = get_current_execution_id()

        with self._lock:
            # tx stream
            stream = self._get_stream(self._activation_by_tx, e.tx_id, max_streams=self._max_tx_streams)
            stream.append(e)

            # recent tx list (avoid repeats back-to-back)
            if not self._activation_recent or self._activation_recent[-1] != e.tx_id:
                self._activation_recent.append(e.tx_id)

            # tx timing bookkeeping
            if e.event is ActivationEventType.START:
                self._tx_started_at[e.tx_id] = e.timestamp
                self._tx_root[e.tx_id] = e.root_id

            # metrics (duration)
            if e.event is ActivationEventType.END:
                start = self._tx_started_at.pop(e.tx_id, None)
                root = self._tx_root.pop(e.tx_id, e.root_id)  # cleanup on END
                if start is not None:
                    self.metrics.activation_duration_seconds.observe(root, e.timestamp - start)

            # metrics (failures)
            if e.event is ActivationEventType.ROLLBACK:
                root = self._tx_root.get(e.tx_id, e.root_id)  # available until END
                if e.detail and "error=" in e.detail:
                    self.metrics.activation_failures_total.inc(root, 1)

            # execution stream (if bound)
            if exec_id:
                self._lru_set(self._tx_to_execution, e.tx_id, exec_id, max_items=self._max_tx_to_exec)

                ex_stream = self._get_stream(
                    self._execution_events, exec_id, max_streams=self._max_exec_streams
                )
                ex_stream.append(
                    {"kind": "activation", "tx_id": e.tx_id, "data": _safe_serialize(e)}
                )

    def on_plugin_event(self, e: LifecycleEvent) -> None:
        exec_id = get_current_execution_id()

        with self._lock:
            pstream = self._get_stream(
                self._lifecycle_by_plugin, e.plugin_id, max_streams=self._max_plugin_streams
            )
            pstream.append(e)

            if exec_id:
                ex_stream = self._get_stream(
                    self._execution_events, exec_id, max_streams=self._max_exec_streams
                )
                ex_stream.append(
                    {"kind": "lifecycle", "plugin_id": e.plugin_id, "data": _safe_serialize(e)}
                )

    # ---------------- query API for RuntimeHost ----------------

    def recent_activation_txs(self) -> List[str]:
        with self._lock:
            return list(self._activation_recent)

    def activation_trace(self, tx_id: str) -> List[dict]:
        with self._lock:
            stream = self._activation_by_tx.get(tx_id)
            if not stream:
                return []
            # stable snapshot
            return [_safe_serialize(e) for e in list(stream)]

    def plugin_lifecycle(self, plugin_id: str) -> List[dict]:
        with self._lock:
            stream = self._lifecycle_by_plugin.get(plugin_id)
            if not stream:
                return []
            return [_safe_serialize(e) for e in list(stream)]

    def execution_trace(self, execution_id: str) -> List[dict]:
        with self._lock:
            stream = self._execution_events.get(execution_id)
            if not stream:
                return []
            return list(stream)

    def execution_for_tx(self, tx_id: str) -> Optional[str]:
        with self._lock:
            return self._tx_to_execution.get(tx_id)

    # ---------------- runtime annotations ----------------

    def note_runtime_warning(self, *, execution_id: str, message: str) -> None:
        with self._lock:
            ex_stream = self._get_stream(self._execution_events, execution_id, max_streams=self._max_exec_streams)
            ex_stream.append(
                {"kind": "runtime_warning", "timestamp": time.time(), "message": message}
            )

    def note_execution_closed(self, *, execution_id: str, reason: str, detail: str | None = None) -> None:
        with self._lock:
            ex_stream = self._get_stream(self._execution_events, execution_id, max_streams=self._max_exec_streams)
            ex_stream.append(
                {
                    "kind": "execution_closed",
                    "timestamp": time.time(),
                    "reason": reason,
                    "detail": detail,
                }
            )

    def note_execution_touched(self, *, execution_id: str, where: str, detail: str | None = None) -> None:
        if not self._debug_touch:
            return
        with self._lock:
            ex_stream = self._get_stream(self._execution_events, execution_id, max_streams=self._max_exec_streams)
            ex_stream.append(
                {
                    "kind": "execution_touched",
                    "timestamp": time.time(),
                    "where": where,
                    "detail": detail,
                }
            )
