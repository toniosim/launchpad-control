"""The reconnect loop: device absent at start, unplugged, replugged, restored on exit."""
import queue

import pytest

from launchpad_control import app as app_module
from launchpad_control import config, grid
from launchpad_control.app import ConfigWatcher, serve
from launchpad_control.events import AudioChanged
from launchpad_control.fake_device import FakeDevice
from launchpad_control.integrations.pipewire import AudioState

LAYOUT = config.load(None)


class Session:
    """Context manager handed out by the fake open_device(); records open/close."""

    def __init__(self, log):
        self.dev = FakeDevice()
        self.log = log

    def __enter__(self):
        self.log.append("open")
        return self.dev

    def __exit__(self, *exc):
        self.log.append("close")


class Audio:
    def set_targets(self, *a): pass
    def request_refresh(self): pass


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(app_module, "CONFIG_POLL_S", 0.005)
    monkeypatch.setattr(app_module, "DEVICE_POLL_S", 0.005)


def scripted(answers):
    """present() that returns the scripted answers in order, then simulates Ctrl+C."""
    it = iter(answers)

    def present():
        try:
            return next(it)
        except StopIteration:
            raise KeyboardInterrupt
    return present


def run_serve(answers, events=None, open_device=None):
    log, sessions = [], []

    def default_open():
        sessions.append(Session(log))
        return sessions[-1]

    with pytest.raises(KeyboardInterrupt):
        serve(ConfigWatcher(None), LAYOUT, Audio(), events or queue.Queue(),
              open_device or default_open, scripted(answers))
    return log, sessions


def test_waits_for_device_then_connects():
    log, sessions = run_serve([False, False, True, True])
    assert log == ["open", "close"]  # closed on exit too (Ctrl+C), not leaked
    assert len(sessions) == 1


def test_reconnects_after_unplug():
    # connect, one healthy check, unplug, absent, replug, healthy check, then Ctrl+C
    log, sessions = run_serve([True, True, False, False, True, True])
    assert log == ["open", "close", "open", "close"]
    assert len(sessions) == 2


def test_state_survives_replug_and_repaints():
    events = queue.Queue()
    events.put(AudioChanged(AudioState(mic_muted=True)))
    _, sessions = run_serve([True, True, False, True, True], events)
    mute = LAYOUT.mic_mute_pad
    assert sessions[0].dev.leds[mute] == grid.RED
    assert sessions[1].dev.leds[mute] == grid.RED  # repainted without waiting for PipeWire


def test_audio_changes_while_unplugged_are_not_lost():
    events = queue.Queue()
    events.put(AudioChanged(AudioState(mic_muted=True)))  # arrives while no device
    _, sessions = run_serve([False, True, True], events)
    assert sessions[0].dev.leds[LAYOUT.mic_mute_pad] == grid.RED


def test_open_failure_is_retried():
    attempts = []
    log = []

    def flaky_open():
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("port busy")
        return Session(log)

    run_serve([True, True, True], open_device=flaky_open)
    assert len(attempts) == 2 and log == ["open", "close"]
