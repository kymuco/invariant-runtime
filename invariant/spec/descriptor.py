# invariant/spec/descriptor.py
from __future__ import annotations

from dataclasses import dataclass

from invariant.spec.meta import PluginMeta
from invariant.spec.source import PluginSourceSpec


@dataclass(frozen=True, slots=True)
class PluginDescriptor:
    """
    Internal system descriptor.
    Never exposed directly to users.
    """

    meta: PluginMeta
    source: PluginSourceSpec
