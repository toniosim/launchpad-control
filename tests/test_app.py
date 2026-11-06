from launchpad_control import grid
from launchpad_control.app import MIC_MUTE_PAD, App
from launchpad_control.events import MicMuteChanged
from launchpad_control.fake_device import FakeDevice


class FakeMic:
    def __init__(self):
        self.toggles = 0

    def toggle(self):
        self.toggles += 1


def make_app():
    dev, mic = FakeDevice(), FakeMic()
    app = App(dev, mic)
    dev.set_event_handler(app.step)
    return app, dev, mic


def test_pad_off_until_state_known():
    app, dev, _ = make_app()
    app.refresh()
    assert dev.leds == {}


def test_press_toggles_but_does_not_change_led():
    app, dev, mic = make_app()
    dev.emit(MicMuteChanged(False))
    dev.press(MIC_MUTE_PAD)
    assert mic.toggles == 1
    # LED still shows the last confirmed state until PipeWire reports back
    assert dev.leds[MIC_MUTE_PAD] == grid.DIM_GREEN


def test_led_follows_external_mute():
    app, dev, mic = make_app()
    dev.emit(MicMuteChanged(True))  # e.g. muted from the keyboard
    assert dev.leds[MIC_MUTE_PAD] == grid.RED
    assert mic.toggles == 0
    dev.emit(MicMuteChanged(False))
    assert dev.leds[MIC_MUTE_PAD] == grid.DIM_GREEN


def test_repeated_state_sends_nothing():
    app, dev, _ = make_app()
    dev.emit(MicMuteChanged(True))
    dev.emit(MicMuteChanged(True))
    dev.emit(MicMuteChanged(True))
    assert len(dev.sent) == 1


def test_other_pads_ignored():
    app, dev, mic = make_app()
    dev.press(11)
    assert mic.toggles == 0
