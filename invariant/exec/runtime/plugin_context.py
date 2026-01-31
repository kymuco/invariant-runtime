# invariant/exec/runtime/plugin_context.py
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Optional

from invariant.spec.context import PluginContext, EmitEvent


@dataclass(frozen=True, slots=True)
class DefaultPluginContext(PluginContext):
    """
    Capability-only context for plugins.
    """
    runtime_id: str
    config: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    emit_event: Optional[EmitEvent] = None

    # можно добавить мягкие, безопасные вещи:
    # clock / metrics / resource handles (потом)



# from __future__ import annotations

# from dataclasses import dataclass
# from typing import Any, Callable, Mapping, Optional


# EmitEvent = Callable[[str], None]  # event_name
# # при желании расширишь: Callable[[str, Mapping[str, Any]], None]


# @dataclass(frozen=True, slots=True)
# class PluginContext:
#     """
#     Capability-only context for plugins.

#     Principles:
#     - no registry access
#     - no runtime.get/resolve
#     - only safe capabilities (logging/config/events)
#     """

#     runtime_id: str
#     config: Mapping[str, Any] = ()
#     emit_event: Optional[EmitEvent] = None

#     # можно добавить мягкие, безопасные вещи:
#     # clock / metrics / resource handles (потом)

