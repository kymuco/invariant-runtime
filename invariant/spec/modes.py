# invariant/spec/modes.py
from enum import Enum, auto

class InstanceMode(Enum):
    MANAGED = auto()
    EPHEMERAL = auto()
    