# invariant/ops/watchdog.py
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

log = logging.getLogger("invariant.watchdog")

class Watchdog:
    """
    Calls a callback periodically in a background thread.
    Designed for maintenance tasks like TTL cleanup.

    - Starts/stops explicitly
    - Never raises to caller (containment)
    """

    def __init__(self, *, interval_seconds: int, callback: Callable[[], None]):
        self._interval = max(1, int(interval_seconds))
        self._callback = callback

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="invariant-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=self._interval + 2)

    def _run(self) -> None:
        # drift-resistant scheduling using monotonic clock
        next_ts = time.monotonic() + self._interval
        while not self._stop.is_set():
            now = time.monotonic()
            sleep_for = next_ts - now
            if sleep_for > 0:
                self._stop.wait(timeout=sleep_for)

            if self._stop.is_set():
                break

            try:
                self._callback()
            except Exception:
                # containment: watchdog must never kill the process
                log.exception("Watchdog callback failed")

            # keep cadence even if callback took time
            next_ts += self._interval
            if next_ts < time.monotonic():
                next_ts = time.monotonic() + self._interval
