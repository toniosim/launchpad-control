from launchpad_control import grid
from launchpad_control.widgets import Fader

F = Fader(col=1, color=grid.GREEN)


def column(frame):
    return [frame[grid.idx(r, 1)] for r in range(1, 9)]


def test_contains_only_its_column():
    assert F.contains(11) and F.contains(81)
    assert not F.contains(12) and not F.contains(1) and not F.contains(91)


def test_full_and_empty():
    assert column(F.render(1.0)) == [grid.GREEN] * 8
    assert grid.GREEN not in column(F.render(0.0))


def test_partial_top_pad_is_dimmer():
    leds = column(F.render(0.3))  # 2.4 pads
    assert leds[:2] == [grid.GREEN] * 2
    track = grid.scale(grid.GREEN, 0.08)
    assert track[1] < leds[2][1] < grid.GREEN[1]
    assert leds[3:] == [track] * 5


def test_above_100_percent_caps():
    assert column(F.render(1.5)) == [grid.GREEN] * 8


def test_unavailable_shows_only_dim_bottom():
    assert list(F.render(None)) == [11]


def test_press_levels():
    assert F.level_for_press(81, 0.5) == 1.0
    assert F.level_for_press(41, 0.5) == 0.5
    assert F.level_for_press(11, 0.5) == 0.125
    assert F.level_for_press(11, 0.125) == 0.0  # bottom pad again -> silent
    assert F.level_for_press(11, 0.0) == 0.0
