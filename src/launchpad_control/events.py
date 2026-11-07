"""Events flowing into the app's main loop, from the device and from integrations."""
from __future__ import annotations

from dataclasses import dataclass

from .integrations.discord import DiscordState
from .integrations.pipewire import AudioState


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
class AudioChanged:
    """A fresh snapshot of audio state from PipeWire."""
    state: AudioState


@dataclass(frozen=True)
class DiscordChanged:
    """Discord connection / voice state changed."""
    state: DiscordState


Event = PadPress | PadRelease | PadPressure | ButtonChange | AudioChanged | DiscordChanged
