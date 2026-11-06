import pytest

from launchpad_control import grid


def test_idx_corners():
    assert grid.idx(1, 1) == 11
    assert grid.idx(1, 8) == 18
    assert grid.idx(8, 1) == 81
    assert grid.idx(8, 8) == 88


def test_idx_out_of_range():
    with pytest.raises(ValueError):
        grid.idx(0, 1)


def test_diff_sends_only_changes():
    d = grid.FrameDiffer()
    assert d.diff({11: (127, 0, 0), 12: (0, 0, 127)}) == [(11, 127, 0, 0), (12, 0, 0, 127)]
    assert d.diff({11: (127, 0, 0), 12: (0, 0, 127)}) == []
    assert d.diff({11: (127, 0, 0), 12: (0, 127, 0)}) == [(12, 0, 127, 0)]


def test_diff_turns_off_removed_leds():
    d = grid.FrameDiffer()
    d.diff({11: (127, 0, 0)})
    assert d.diff({}) == [(11, 0, 0, 0)]
    assert d.diff({}) == []


def test_diff_clamps_colours():
    d = grid.FrameDiffer()
    assert d.diff({11: (300, -5, 64)}) == [(11, 127, 0, 64)]
