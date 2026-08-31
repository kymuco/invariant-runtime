from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class RpcTone(str, Enum):
    NEUTRAL = "neutral"
    WARM = "warm"


@dataclass(frozen=True, slots=True)
class RpcMarker:
    offset: int
    label: str


@dataclass(frozen=True, slots=True)
class RpcRequest:
    text: str
    tone: RpcTone
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RpcResult:
    value: str
    markers: tuple[RpcMarker, ...]
    metadata: dict[str, Any]
