from __future__ import annotations

import time
from enum import Enum, auto
from dataclasses import dataclass
from typing import Callable, List, Optional
import logging


log = logging.getLogger(__name__)

# -------------------- states --------------------

class PluginState(Enum):
    CREATED = auto()
    INITIALIZING = auto()
    READY = auto()
    SHUTTING_DOWN = auto()
    SHUTDOWN = auto()
    FAILED = auto()


# -------------------- transitions --------------------

VALID_TRANSITIONS: dict[PluginState, set[PluginState]] = {
    PluginState.CREATED: {PluginState.INITIALIZING},
    PluginState.INITIALIZING: {PluginState.READY, PluginState.FAILED},
    PluginState.READY: {PluginState.SHUTTING_DOWN},
    PluginState.SHUTTING_DOWN: {PluginState.SHUTDOWN, PluginState.FAILED},
    PluginState.SHUTDOWN: set(),
    PluginState.FAILED: set(),
}


# -------------------- events --------------------

@dataclass(frozen=True)
class LifecycleEvent:
    plugin_id: str
    version: str | None
    state: PluginState
    timestamp: float
    error: Exception | None = None


# -------------------- errors --------------------

class InvalidTransition(RuntimeError):
    pass


# -------------------- FSM --------------------

class PluginFSM:
    """
    Finite state machine for plugin lifecycle.

    Invariants:
    - single source of truth for plugin state
    - transitions are strictly validated
    - FAILED and SHUTDOWN are terminal states
    """

    def __init__(
        self,
        *,
        plugin_id: str,
        version: str | None,
    ) -> None:
        self.plugin_id = plugin_id
        self.version = version
        self.state: PluginState = PluginState.CREATED
        self._listeners: List[Callable[[LifecycleEvent], None]] = []

    # ---------- listeners ----------

    def on_event(self, callback: Callable[[LifecycleEvent], None]) -> None:
        if callback is None:
            raise TypeError("callback must not be None")
        self._listeners.append(callback)

    # ---------- transitions ----------

    def transition(
        self,
        new_state: PluginState,
        *,
        error: Exception | None = None,
    ) -> None:
        allowed = VALID_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise InvalidTransition(
                f"Invalid transition {self.state.name} → {new_state.name} "
                f"for {self.plugin_id}@{self.version}"
            )

        self.state = new_state

        event = LifecycleEvent(
            plugin_id=self.plugin_id,
            version=self.version,
            state=new_state,
            timestamp=time.time(),
            error=error,
        )

        for cb in self._listeners:
            try:
                cb(event)
            except Exception:
                # Never allow observers to break lifecycle.
                log.exception(
                    "Lifecycle listener failed for %s@%s state=%s",
                    self.plugin_id,
                    self.version,
                    new_state.name,
                )

    # ---------- helpers ----------

    @property
    def is_terminal(self) -> bool:
        return self.state in (PluginState.SHUTDOWN, PluginState.FAILED)

    @property
    def is_ready(self) -> bool:
        return self.state is PluginState.READY
