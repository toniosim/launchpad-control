"""Events flowing into the app's main loop, from the device and from integrations."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PadPress:
    idx: int
    velocity: int


@dataclass(frozen=True)
class PadRelease:
    idx: int


@dataclass(frozen=True)
class PadPressure:
    idx: int
    value: int


@dataclass(frozen=True)
class ButtonChange:
    """Edge buttons arrive as control_change. CC numbers are not mapped yet."""
    cc: int
    value: int


@dataclass(frozen=True)
class MicMuteChanged:
    muted: bool


Event = PadPress | PadRelease | PadPressure | ButtonChange | MicMuteChanged
