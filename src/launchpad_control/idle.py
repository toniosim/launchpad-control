"""Idle animations ("screensavers") for the grid. Pure: they only produce frames.

Each mode has `step()` (advance one frame) and `render() -> Frame`. The app decides
when to start one and how often to step it.
"""
from __future__ import annotations

import random
from typing import Callable, Protocol

from . import grid
from .grid import RGB, Frame

Cell = tuple[int, int]  # (row, col), 1-8 like grid.idx


class IdleMode(Protocol):
    name: str

    def step(self) -> None: ...
    def render(self) -> Frame: ...


# Colour by age: newborns are pale, then they mature from green through teal to blue.
LIFE_AGE_COLORS: tuple[RGB, ...] = (
    (90, 127, 90),
    (20, 127, 25),
    (0, 115, 50),
    (0, 95, 85),
    (0, 70, 115),
    (15, 40, 127),
    (40, 20, 127),
)
TRAIL_BRIGHTNESS = 0.1   # a cell that just died glows faintly for one generation


class Life:
    """Conway's Game of Life on the 8x8 grid, wrapped into a torus.

    A board this small dies out or settles into a loop quickly, so it reseeds itself:
    after dying out, after repeating an earlier state (still life, oscillator, or a
    glider lapping the torus) for `linger` more generations, or after `max_generations`.
    """

    name = "life"

    def __init__(self, rng: random.Random | None = None, *, size: int = 8,
                 density: float = 0.35, linger: int = 12, max_generations: int = 300,
                 seed: set[Cell] | None = None) -> None:
        self.rng = rng or random.Random()
        self.size = size
        self.density = density
        self.linger = linger
        self.max_generations = max_generations
        self.reseeds = 0
        self._start(seed)

    # ---- public --------------------------------------------------------------

    @property
    def alive(self) -> set[Cell]:
        return set(self.ages)

    def step(self) -> None:
        if self._should_reseed():
            self.reseeds += 1
            self._start(None)
            return
        born_or_kept = self._next_generation()
        self.trail = {c: self._color(a) for c, a in self.ages.items() if c not in born_or_kept}
        self.ages = {c: self.ages.get(c, -1) + 1 for c in born_or_kept}
        self.generation += 1
        state = frozenset(self.ages)
        if self._repeat_at is None and state in self._seen:
            self._repeat_at = self.generation
        self._seen.add(state)

    def render(self) -> Frame:
        frame: Frame = {grid.idx(*c): grid.scale(rgb, TRAIL_BRIGHTNESS)
                        for c, rgb in self.trail.items()}
        frame.update({grid.idx(*c): self._color(a) for c, a in self.ages.items()})
        return frame

    # ---- internals -----------------------------------------------------------

    def _start(self, seed: set[Cell] | None) -> None:
        if seed is None:
            seed = {(r, c) for r in range(1, self.size + 1) for c in range(1, self.size + 1)
                    if self.rng.random() < self.density}
        self.ages: dict[Cell, int] = {c: 0 for c in seed}
        self.trail: dict[Cell, RGB] = {}
        self.generation = 0
        self._seen: set[frozenset[Cell]] = {frozenset(self.ages)}
        self._repeat_at: int | None = None

    def _should_reseed(self) -> bool:
        if not self.ages:
            return not self.trail  # let the last trail fade first
        if self._repeat_at is not None and self.generation - self._repeat_at >= self.linger:
            return True
        return self.generation >= self.max_generations

    def _next_generation(self) -> set[Cell]:
        n = self.size
        counts: dict[Cell, int] = {}
        for r, c in self.ages:
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr or dc:
                        nb = ((r - 1 + dr) % n + 1, (c - 1 + dc) % n + 1)
                        counts[nb] = counts.get(nb, 0) + 1
        return {cell for cell, k in counts.items()
                if k == 3 or (k == 2 and cell in self.ages)}

    @staticmethod
    def _color(age: int) -> RGB:
        return LIFE_AGE_COLORS[min(age, len(LIFE_AGE_COLORS) - 1)]


MODES: dict[str, Callable[[random.Random], IdleMode]] = {
    "life": lambda rng: Life(rng),
}
