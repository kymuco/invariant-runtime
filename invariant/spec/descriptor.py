# invariant/spec/descriptor.py
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from invariant.spec.source import PluginSourceSpec

if TYPE_CHECKING:
    from invariant.spec.meta import PluginMeta
else:
    PluginMeta = Any


@dataclass(frozen=True, slots=True)
class PluginDescriptor:
    """
    Internal system descriptor.
    Never exposed directly to users.
    """

    meta: PluginMeta
    source: PluginSourceSpec
