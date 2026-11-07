import pytest

from launchpad_control import grid
from launchpad_control import config
from launchpad_control.app import App
from launchpad_control.events import AudioChanged
from launchpad_control.fake_device import FakeDevice
from launchpad_control.integrations.pipewire import AudioState

LAYOUT = config.load(None)  # the packaged default layout
MIC_MUTE_PAD = LAYOUT.mic_mute_pad
MASTER_FADER = LAYOUT.master
APP_FADERS = LAYOUT.apps


class FakeAudio:
    def __init__(self):
        self.calls = []

    def toggle_mic_mute(self):
        self.calls.append(("mute",))

    def set_master_volume(self, level):
        self.calls.append(("master", level))

    def set_app_volume(self, name, level):
        self.calls.append(("app", name, level))

    def set_active_volume(self, level):
        self.calls.append(("active", level))


def make_app():
    dev, audio = FakeDevice(), FakeAudio()
    app = App(dev, audio, LAYOUT)
    dev.set_event_handler(app.step)
    return app, dev, audio


def state(**kw):
    kw.setdefault("app_volumes", {"firefox": 0.5, "discord": None})
    return AudioChanged(AudioState(**kw))


def col_leds(dev, col):
    return [dev.leds.get(grid.idx(r, col)) for r in range(1, 9)]


# ---- mic mute ----

def test_only_dim_placeholders_until_state_known():
    app, dev, _ = make_app()
    app.refresh()
    assert MIC_MUTE_PAD not in dev.leds
    # each fader shows just its dim bottom pad
    assert set(dev.leds) == {grid.idx(1, f.col) for f in LAYOUT.faders()}


def test_mute_press_toggles_but_does_not_change_led():
    app, dev, audio = make_app()
    dev.emit(state(mic_muted=False))
    dev.press(MIC_MUTE_PAD)
    assert audio.calls == [("mute",)]
    assert dev.leds[MIC_MUTE_PAD] == grid.DIM_GREEN


def test_mute_led_follows_external_change():
    app, dev, audio = make_app()
    dev.emit(state(mic_muted=True))
    assert dev.leds[MIC_MUTE_PAD] == grid.RED
    dev.emit(state(mic_muted=False))
    assert dev.leds[MIC_MUTE_PAD] == grid.DIM_GREEN
    assert audio.calls == []


def test_repeated_state_sends_nothing():
    app, dev, _ = make_app()
    for _ in range(3):
        dev.emit(state(mic_muted=True, master_volume=0.5))
    assert len(dev.sent) == 1


# ---- master volume ----

def test_master_fader_shows_level():
    app, dev, _ = make_app()
    dev.emit(state(master_volume=0.75))
    leds = col_leds(dev, MASTER_FADER.col)
    assert leds[:6] == [grid.GREEN] * 6
    assert all(c != grid.GREEN for c in leds[6:])


def test_master_fader_press_sets_volume():
    app, dev, audio = make_app()
    dev.emit(state(master_volume=0.75))
    dev.press(grid.idx(4, MASTER_FADER.col))
    assert audio.calls == [("master", 0.5)]


def test_master_follows_external_change():
    app, dev, _ = make_app()
    dev.emit(state(master_volume=1.0))
    dev.emit(state(master_volume=0.25))
    leds = col_leds(dev, MASTER_FADER.col)
    assert leds[:2] == [grid.GREEN] * 2 and leds[2] != grid.GREEN


# ---- per-app volume ----

def test_app_fader_press_sets_app_volume():
    app, dev, audio = make_app()
    firefox = APP_FADERS[0]
    dev.emit(state())
    dev.press(grid.idx(8, firefox.fader.col))
    assert audio.calls == [("app", "firefox", 1.0)]


def test_app_without_stream_ignores_press():
    app, dev, audio = make_app()
    discord = APP_FADERS[1]
    dev.emit(state())
    dev.press(grid.idx(5, discord.fader.col))
    assert audio.calls == []


def test_app_colours():
    app, dev, _ = make_app()
    dev.emit(state(app_volumes={"firefox": 1.0, "discord": 1.0}))
    assert col_leds(dev, APP_FADERS[0].fader.col) == [grid.ORANGE] * 8
    assert col_leds(dev, APP_FADERS[1].fader.col) == [grid.PURPLE] * 8


def test_default_layout_matches_spec():
    assert MIC_MUTE_PAD == 18
    assert (MASTER_FADER.col, MASTER_FADER.color) == (1, grid.GREEN)
    assert [(a.name, a.fader.col, a.fader.color) for a in APP_FADERS] == [
        ("firefox", 2, grid.ORANGE), ("discord", 3, grid.PURPLE)]
    assert LAYOUT.active_app.fader.col == 4


def test_layout_swap_takes_effect():
    app, dev, audio = make_app()
    dev.emit(state(mic_muted=True))
    app.layout = config.parse({"mic_mute": {"pad": [8, 8]}})
    app.refresh()
    assert dev.leds == {88: grid.RED}  # faders gone, mute moved
    dev.press(88)
    assert audio.calls == [("mute",)]


# ---- active app ----

def test_active_app_fader():
    app, dev, audio = make_app()
    col = LAYOUT.active_app.fader.col
    dev.emit(state(active_app="Game", active_volume=0.5))
    assert col_leds(dev, col)[:4] == [LAYOUT.active_app.fader.color] * 4
    dev.press(grid.idx(8, col))
    assert audio.calls == [("active", 1.0)]


def test_active_app_press_ignored_when_nothing_playing():
    app, dev, audio = make_app()
    dev.emit(state())
    dev.press(grid.idx(8, LAYOUT.active_app.fader.col))
    assert audio.calls == []
