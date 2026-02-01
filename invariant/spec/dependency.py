# invariant/spec/dependency.py
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from invariant.spec.ids import canonical_id_or_ref


class DependencyScope(Enum):
    """
    Lifetime of a dependency instance.
    """

    SINGLETON = auto()   # one per application
    EXECUTION = auto()   # one per ExecutionContext
    PLUGIN = auto()      # one per plugin instance
    TRANSIENT = auto()   # new instance per resolve


@dataclass(frozen=True, slots=True)
class DependencySpec:
    """
    Declarative dependency specification.

    Invariants:
    - no lifecycle logic
    - no instance mode
    - no runtime knowledge
    """

    id: str                 # plugin_id of dependency
    param: str              # argument name in initialize(...)
    scope: DependencyScope = DependencyScope.EXECUTION
    optional: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("DependencySpec.id must be non-empty string")
        if not isinstance(self.param, str) or not self.param.strip():
            raise ValueError("DependencySpec.param must be non-empty string")
        if not self.param.isidentifier():
            raise ValueError(f"DependencySpec.param must be valid identifier, got {self.param!r}")

        object.__setattr__(self, "id", canonical_id_or_ref(self.id))
