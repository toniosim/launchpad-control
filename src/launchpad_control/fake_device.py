"""In-memory stand-in for LaunchpadDevice, for tests and running without hardware."""
from __future__ import annotations

from typing import Iterable

from .device import EventHandler
from .events import Event, PadPress, PadRelease
from .grid import RGB, LedSpec


class FakeDevice:
    def __init__(self) -> None:
        self._handler: EventHandler | None = None
        self.sent: list[list[LedSpec]] = []  # one entry per set_leds call
        self.leds: dict[int, RGB] = {}       # current LED state as the device would show it

    def set_event_handler(self, handler: EventHandler) -> None:
        self._handler = handler

    def set_leds(self, specs: Iterable[LedSpec]) -> None:
        specs = list(specs)
        if not specs:
            return
        self.sent.append(specs)
        for i, r, g, b in specs:
            if (r, g, b) == (0, 0, 0):
                self.leds.pop(i, None)
            else:
                self.leds[i] = (r, g, b)

    # Test helpers
    def emit(self, event: Event) -> None:
        assert self._handler is not None, "no event handler set"
        self._handler(event)

    def press(self, idx: int, velocity: int = 100) -> None:
        self.emit(PadPress(idx, velocity))
        self.emit(PadRelease(idx))
