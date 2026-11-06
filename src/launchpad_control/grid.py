"""Grid indices, colours, and the frame buffer that turns state into minimal LED updates."""
from __future__ import annotations

RGB = tuple[int, int, int]
LedSpec = tuple[int, int, int, int]  # (idx, r, g, b)

OFF: RGB = (0, 0, 0)
RED: RGB = (127, 0, 0)
DIM_GREEN: RGB = (0, 20, 0)


def idx(row: int, col: int) -> int:
    """Programmer-mode index for an 8x8 pad. Rows/cols 1-8, bottom-left = 11."""
    if not (1 <= row <= 8 and 1 <= col <= 8):
        raise ValueError(f"row/col out of range: {row}, {col}")
    return row * 10 + col


def clamp(rgb: RGB) -> RGB:
    """Device colours are 0-127 per channel."""
    return tuple(max(0, min(127, int(c))) for c in rgb)  # type: ignore[return-value]


Frame = dict[int, RGB]
"""A full picture of what the LEDs should show. Missing indices mean off."""


class FrameDiffer:
    """Remembers what was last sent and returns only the LEDs that changed."""

    def __init__(self) -> None:
        self._last: Frame = {}

    def diff(self, frame: Frame) -> list[LedSpec]:
        new = {i: clamp(c) for i, c in frame.items() if clamp(c) != OFF}
        changes: list[LedSpec] = []
        for i, rgb in new.items():
            if self._last.get(i) != rgb:
                changes.append((i, *rgb))
        for i in self._last.keys() - new.keys():
            changes.append((i, *OFF))
        self._last = new
        return sorted(changes)
