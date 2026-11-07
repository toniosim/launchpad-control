"""Reusable pad-grid widgets. Pure functions of state: no device or system access."""
from __future__ import annotations

from dataclasses import dataclass

from . import grid
from .grid import RGB, Frame

TRACK_BRIGHTNESS = 0.08     # unlit part of a fader, so you can see where it is
MIN_PARTIAL = 0.15          # dimmest a partially filled top pad gets


@dataclass(frozen=True)
class Fader:
    """A vertical 8-pad fader in one grid column. Bottom = 0%, lit height = level.

    Press row r -> level r/8. Pressing the bottom pad when already at or below 1/8 -> 0.
    """
    col: int
    color: RGB

    def contains(self, idx: int) -> bool:
        return idx % 10 == self.col and 1 <= idx // 10 <= 8

    def level_for_press(self, idx: int, current: float | None) -> float:
        row = idx // 10
        if row == 1 and current is not None and current <= 1 / 8 + 1e-6:
            return 0.0
        return row / 8

    def render(self, level: float | None) -> Frame:
        if level is None:
            # Unavailable (e.g. the app isn't playing audio): only a dim bottom pad.
            return {grid.idx(1, self.col): grid.scale(self.color, TRACK_BRIGHTNESS)}
        filled = min(max(level, 0.0), 1.0) * 8 + 1e-6
        frame: Frame = {}
        for row in range(1, 9):
            amount = filled - (row - 1)  # how much of this pad is "filled", 0..1
            if amount >= 1:
                brightness = 1.0
            elif amount > 0.01:
                brightness = max(MIN_PARTIAL, amount)
            else:
                brightness = TRACK_BRIGHTNESS
            frame[grid.idx(row, self.col)] = grid.scale(self.color, brightness)
        return frame
