# invariant/ops/metrics.py
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import threading


@dataclass
class Counter:
    value: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def inc(self, n: int = 1) -> None:
        with self._lock:
            self.value += int(n)

    def snapshot(self) -> int:
        with self._lock:
            return int(self.value)


@dataclass
class LabeledCounter:
    values: Dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def inc(self, label: str, n: int = 1) -> None:
        k = str(label)
        with self._lock:
            self.values[k] = self.values.get(k, 0) + int(n)

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return dict(self.values)


@dataclass
class Gauge:
    value: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def inc(self, n: int = 1) -> None:
        with self._lock:
            self.value += int(n)

    def dec(self, n: int = 1) -> None:
        with self._lock:
            self.value -= int(n)
            if self.value < 0:
                self.value = 0

    def set(self, v: int) -> None:
        with self._lock:
            self.value = int(v)

    def snapshot(self) -> int:
        with self._lock:
            return int(self.value)


@dataclass
class Histogram:
    """
    Minimal histogram: keeps count/sum/min/max.
    """
    count: int = 0
    total: float = 0.0
    min: Optional[float] = None
    max: Optional[float] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def observe(self, x: float) -> None:
        v = float(x)
        with self._lock:
            self.count += 1
            self.total += v
            self.min = v if self.min is None else min(self.min, v)
            self.max = v if self.max is None else max(self.max, v)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            avg = (self.total / self.count) if self.count else 0.0
            return {
                "count": self.count,
                "sum": self.total,
                "min": self.min,
                "max": self.max,
                "avg": avg,
            }


@dataclass
class LabeledHistogram:
    values: Dict[str, Histogram] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def observe(self, label: str, x: float) -> None:
        k = str(label)
        with self._lock:
            h = self.values.get(k)
            if h is None:
                h = Histogram()
                self.values[k] = h
        # observe outside dict-lock (Histogram has its own lock)
        h.observe(x)

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            items = list(self.values.items())
        return {k: h.snapshot() for k, h in items}


@dataclass
class RuntimeMetrics:
    # execution
    executions_active: int = 0
    executions_created_total: Counter = field(default_factory=Counter)
    executions_closed_total: LabeledCounter = field(default_factory=LabeledCounter)
    executions_expired_total: Counter = field(default_factory=Counter)
    executions_failed_total: Counter = field(default_factory=Counter)
    execution_limit_exceeded_total: Counter = field(default_factory=Counter)

    # activation
    activation_duration_seconds: LabeledHistogram = field(default_factory=LabeledHistogram)  # root plugin_id
    activation_failures_total: LabeledCounter = field(default_factory=LabeledCounter)       # root plugin_id

    # plugin calls
    plugin_call_duration_seconds: LabeledHistogram = field(default_factory=LabeledHistogram)  # "plugin.method"
    plugin_call_errors_total: LabeledCounter = field(default_factory=LabeledCounter)          # "plugin.method"

    # http
    http_inflight: Gauge = field(default_factory=Gauge)
    http_requests_total: Counter = field(default_factory=Counter)
    http_busy_rejected_total: Counter = field(default_factory=Counter)
    http_responses_total_by_status: LabeledCounter = field(default_factory=LabeledCounter)
    http_request_duration_seconds: LabeledHistogram = field(default_factory=LabeledHistogram)  # "METHOD route"

    started_at: float = field(default_factory=lambda: time.time())

    def snapshot(self) -> Dict[str, Any]:
        active = int(self.executions_active)
        started_at = float(self.started_at)

        return {
            "uptime_s": time.time() - started_at,
            "executions": {
                "active": active,
                "created_total": self.executions_created_total.snapshot(),
                "closed_total_by_reason": self.executions_closed_total.snapshot(),
                "expired_total": self.executions_expired_total.snapshot(),
                "failed_total": self.executions_failed_total.snapshot(),
                "limit_exceeded_total": self.execution_limit_exceeded_total.snapshot(),
            },
            "activation": {
                "duration_seconds_by_root": self.activation_duration_seconds.snapshot(),
                "failures_total_by_root": self.activation_failures_total.snapshot(),
            },
            "plugin_calls": {
                "duration_seconds_by_call": self.plugin_call_duration_seconds.snapshot(),
                "errors_total_by_call": self.plugin_call_errors_total.snapshot(),
            },
            "http": {
                "inflight": self.http_inflight.snapshot(),
                "requests_total": self.http_requests_total.snapshot(),
                "busy_rejected_total": self.http_busy_rejected_total.snapshot(),
                "responses_total_by_status": self.http_responses_total_by_status.snapshot(),
                "request_duration_seconds_by_route": self.http_request_duration_seconds.snapshot(),
            },
        }
