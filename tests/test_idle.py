import random

import pytest

from launchpad_control import config, grid
from launchpad_control.app import App
from launchpad_control.config import ConfigError
from launchpad_control.events import AudioChanged, ButtonChange, PadPress, PadRelease
from launchpad_control.fake_device import FakeDevice
from launchpad_control.idle import LIFE_AGE_COLORS, TRAIL_BRIGHTNESS, Life
from launchpad_control.integrations.pipewire import AudioState

BLINKER = {(4, 3), (4, 4), (4, 5)}
BLOCK = {(2, 2), (2, 3), (3, 2), (3, 3)}


def life(seed, **kw):
    return Life(random.Random(1), seed=set(seed), **kw)


# ---- Life rules ----

def test_blinker_oscillates():
    g = life(BLINKER)
    g.step()
    assert g.alive == {(3, 4), (4, 4), (5, 4)}
    g.step()
    assert g.alive == BLINKER


def test_block_is_still():
    g = life(BLOCK)
    g.step()
    assert g.alive == BLOCK


def test_board_wraps_around():
    # a blinker straddling the left/right edge still works on the torus
    g = life({(4, 8), (4, 1), (4, 2)})
    g.step()
    assert g.alive == {(3, 1), (4, 1), (5, 1)}


def test_glider_moves():
    glider = {(8, 2), (7, 3), (6, 1), (6, 2), (6, 3)}
    g = life(glider)
    for _ in range(4):
        g.step()
    assert g.alive == {(r - 1, c + 1) for r, c in glider}  # one cell down-right


# ---- reseeding ----

def test_reseeds_after_dying_out():
    g = life({(4, 4)})
    g.step()  # dies, leaves a trail
    assert g.alive == set() and g.trail and g.reseeds == 0
    g.step()  # trail fades
    g.step()
    assert g.reseeds == 1 and g.alive


def test_still_life_lingers_then_reseeds():
    g = life(BLOCK, linger=5)
    for _ in range(6):  # repeat noticed at generation 1, then 5 more shown
        g.step()
        assert g.reseeds == 0
    g.step()
    assert g.reseeds == 1


def test_oscillator_lingers_then_reseeds():
    g = life(BLINKER, linger=4)
    for _ in range(6):  # repeat noticed at generation 2, then 4 more shown
        g.step()
    assert g.reseeds == 0
    g.step()
    assert g.reseeds == 1


def test_generation_cap():
    glider = {(8, 2), (7, 3), (6, 1), (6, 2), (6, 3)}
    g = life(glider, linger=1000, max_generations=10)
    for _ in range(10):
        g.step()
    assert g.reseeds == 0
    g.step()
    assert g.reseeds == 1


def test_random_seed_is_reproducible():
    a, b = Life(random.Random(7)), Life(random.Random(7))
    for _ in range(50):
        a.step(), b.step()
    assert a.alive == b.alive


# ---- colours ----

def test_render_colours_by_age_and_trails():
    g = life(BLINKER)
    assert set(g.render().values()) == {LIFE_AGE_COLORS[0]}
    g.step()
    frame = g.render()
    assert frame[grid.idx(4, 4)] == LIFE_AGE_COLORS[1]          # survivor aged
    assert frame[grid.idx(5, 4)] == LIFE_AGE_COLORS[0]          # newborn
    assert frame[grid.idx(4, 3)] == grid.scale(LIFE_AGE_COLORS[0], TRAIL_BRIGHTNESS)  # just died


# ---- config ----

def test_config_idle_defaults():
    layout = config.parse({"idle": {"after": 60}})
    assert (layout.idle.after, layout.idle.mode, layout.idle.interval) == (60, "life", 0.4)


def test_default_config_has_idle():
    assert config.load(None).idle.mode == "life"


@pytest.mark.parametrize("section", [
    {},
    {"after": 0},
    {"after": "5m"},
    {"after": 60, "mode": "pong"},
    {"after": 60, "interval": -1},
    {"after": 60, "speed": 2},
])
def test_config_idle_rejects(section):
    with pytest.raises(ConfigError):
        config.parse({"idle": section})


# ---- in the app ----

class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Audio:
    def __init__(self):
        self.calls = []

    def toggle_mic_mute(self):
        self.calls.append("mute")


LAYOUT = config.parse({"mic_mute": {"pad": [1, 8]},
                       "idle": {"after": 60, "interval": 0.5}})


def make_app(layout=LAYOUT):
    clock, dev, audio = Clock(), FakeDevice(), Audio()
    app = App(dev, audio, layout, clock=clock, rng=random.Random(3))
    dev.set_event_handler(app.step)
    dev.emit(AudioChanged(AudioState(mic_muted=True)))
    return app, dev, audio, clock


def test_goes_idle_after_timeout():
    app, dev, _, clock = make_app()
    clock.t += 59
    app.step()
    assert app.idle is None and dev.leds == {18: grid.RED}
    assert app.seconds_until_tick() == pytest.approx(1)
    clock.t += 1
    app.step()
    assert isinstance(app.idle, Life)
    assert dev.leds == app.idle.render()


def test_animation_advances_on_interval():
    app, dev, _, clock = make_app()
    clock.t += 60
    app.step()
    assert app.idle.generation == 0
    clock.t += 0.4
    app.step()
    assert app.idle.generation == 0
    assert app.seconds_until_tick() == pytest.approx(0.1)
    clock.t += 0.1
    app.step()
    assert app.idle.generation == 1


def test_press_wakes_and_is_swallowed():
    app, dev, audio, clock = make_app()
    clock.t += 60
    app.step()
    dev.press(18)
    assert app.idle is None and audio.calls == []
    assert dev.leds == {18: grid.RED}   # normal view back
    dev.press(18)
    assert audio.calls == ["mute"]      # next press acts normally


def test_edge_button_wakes():
    app, dev, _, clock = make_app()
    clock.t += 60
    app.step()
    dev.emit(ButtonChange(cc=95, value=127))
    assert app.idle is None


def test_activity_resets_timer():
    app, dev, _, clock = make_app()
    clock.t += 50
    dev.press(18)
    clock.t += 50
    app.step()
    assert app.idle is None


def test_releases_and_state_changes_dont_wake():
    app, dev, _, clock = make_app()
    clock.t += 60
    app.step()
    dev.emit(PadRelease(18))
    dev.emit(AudioChanged(AudioState(mic_muted=False)))
    dev.emit(ButtonChange(cc=95, value=0))
    assert app.idle is not None


def test_disabled_without_idle_section():
    app, _, _, clock = make_app(config.parse({"mic_mute": {"pad": [1, 8]}}))
    clock.t += 10_000
    app.step()
    assert app.idle is None
    assert app.seconds_until_tick() == float("inf")


def test_removing_idle_from_config_stops_it():
    app, dev, _, clock = make_app()
    clock.t += 60
    app.step()
    app.layout = config.parse({"mic_mute": {"pad": [1, 8]}})
    app.step()
    assert app.idle is None and dev.leds == {18: grid.RED}


def test_wake_press_during_animation_frame_is_not_a_volume_change():
    # fader press while idle must not set a volume
    layout = config.parse({"master": {"column": 1, "color": "green"},
                           "idle": {"after": 60}})
    clock, dev = Clock(), FakeDevice()

    class A:
        calls = []

        def set_master_volume(self, level):
            self.calls.append(level)

    app = App(dev, A(), layout, clock=clock)
    dev.set_event_handler(app.step)
    clock.t += 60
    app.step()
    dev.emit(PadPress(grid.idx(8, 1), 100))
    assert A.calls == []
