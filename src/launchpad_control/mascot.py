"""The Claude bank's mascot: a little pixel critter after Claude Code's terminal one.

Poses are drawn as text, top row first, 8 columns wide. `#` = body, `!` = alert light,
anything else = off (the eyes are just holes in the body). The sprite stands on row 2;
row 1 is left free for the session pips.
"""
from __future__ import annotations

from . import grid

BODY: grid.RGB = (127, 42, 18)     # terracotta, as close as the LEDs get
ALERT: grid.RGB = (127, 100, 0)    # yellow light over its head: a chat needs you
SLEEPY = 0.3                       # brightness with no sessions at all

STAND = """
........
........
.######.
.#.##.#.
########
.######.
.##..##.
"""

# Walking in place: hop with one pair of legs, land, hop with the other.
STEP_LEFT = """
........
.######.
.#.##.#.
########
.######.
.#...#..
........
"""

STEP_RIGHT = """
........
.######.
.#.##.#.
########
.######.
..#...#.
........
"""

ARMS_UP = """
...!!...
#......#
########
.#.##.#.
.######.
.######.
.##..##.
"""

WALK = (STAND, STEP_LEFT, STAND, STEP_RIGHT)
WAVE = (ARMS_UP, STAND)
TOP_ROW = 8  # the first line of a pose is this grid row


def draw(pose: str, brightness: float = 1.0) -> grid.Frame:
    frame: grid.Frame = {}
    lines = pose.strip("\n").splitlines()
    for i, line in enumerate(lines):
        for col, ch in enumerate(line, start=1):
            if ch == "#":
                frame[grid.idx(TOP_ROW - i, col)] = grid.scale(BODY, brightness)
            elif ch == "!":
                frame[grid.idx(TOP_ROW - i, col)] = ALERT
    return frame


def render(working: bool, asking: bool, alone: bool, tick: int) -> grid.Frame:
    """The critter for the bank's overall state; `tick` counts animation frames.

    asking (a chat needs you) beats working; with neither it stands still, dimmed when
    there are no sessions at all (`alone`).
    """
    if asking:
        return draw(WAVE[(tick // 2) % len(WAVE)])
    if working:
        return draw(WALK[tick % len(WALK)])
    return draw(STAND, SLEEPY if alone else 1.0)
