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


# --- waking from suspend ---------------------------------------------------------------

class Watch:
    """Stands in for SleepWatch: woke() answers from a script, then False."""

    def __init__(self, answers):
        self.answers = list(answers)

    def woke(self):
        return self.answers.pop(0) if self.answers else False


def run_serve_with(answers, watch, events=None):
    log, sessions = [], []

    def open_device():
        sessions.append(Session(log))
        return sessions[-1]

    with pytest.raises(KeyboardInterrupt):
        serve(ConfigWatcher(None), LAYOUT, Audio(), events or queue.Queue(), open_device,
              scripted(answers), sleep_watch=watch)
    return log, sessions


def test_wake_reopens_the_device_even_though_it_never_unplugged(monkeypatch):
    monkeypatch.setattr(app_module, "WAKE_SETTLE_S", 0.01)
    # woke() is consumed once on connect (baseline), then reports a wake on the first poll.
    log, sessions = run_serve_with([True, True, True], Watch([False, True]))
    assert log == ["open", "close", "open", "close"]
    assert len(sessions) == 2


def test_wake_repaints_from_carried_state(monkeypatch):
    monkeypatch.setattr(app_module, "WAKE_SETTLE_S", 0.01)
    events = queue.Queue()
    events.put(AudioChanged(AudioState(mic_muted=True)))
    _, sessions = run_serve_with([True, True, True], Watch([False, False, True]), events)
    mute = LAYOUT.mic_mute_pad
    assert sessions[1].dev.leds[mute] == grid.RED


def test_state_event_arriving_with_the_wake_is_not_lost(monkeypatch):
    monkeypatch.setattr(app_module, "WAKE_SETTLE_S", 0.01)
    events = queue.Queue()
    events.put(AudioChanged(AudioState(mic_muted=True)))
    # the very first poll reports the wake while that event is the one being handled
    _, sessions = run_serve_with([True, True, True], Watch([False, True]), events)
    assert sessions[1].dev.leds[LAYOUT.mic_mute_pad] == grid.RED


def test_sleep_watch_only_counts_time_spent_suspended():
    boot, mono = [100.0], [50.0]
    watch = app_module.SleepWatch(lambda: boot[0], lambda: mono[0])
    boot[0] += 10; mono[0] += 10  # ten awake seconds
    assert not watch.woke()
    boot[0] += 3600  # an hour passes in boottime only: suspended
    assert watch.woke()
    assert not watch.woke()  # reported once
    boot[0] += 1; mono[0] += 1
    assert not watch.woke()


# --- a Launchpad stuck on the USB bus ---------------------------------------------------

class Bus:
    def __init__(self, on_bus=True):
        self.on_bus, self.now = on_bus, 0.0

    def notice(self):
        return app_module.StuckDeviceNotice(lambda: self.on_bus, lambda: self.now)

    def advance(self, notice, seconds, connected=False):
        self.now += seconds
        notice.poll(connected)


def test_stuck_device_is_reported_once_after_the_grace_period(caplog):
    bus = Bus(); notice = bus.notice()
    notice.poll(False)
    bus.advance(notice, app_module.STUCK_GRACE_S - 1)
    assert "Unplug and replug" not in caplog.text  # a normal replug shows up within the grace
    for _ in range(10):
        bus.advance(notice, 5)
    assert caplog.text.count("Unplug and replug") == 1


def test_nothing_reported_when_unplugged_or_connected(caplog):
    bus = Bus(on_bus=False); notice = bus.notice()
    for _ in range(10):
        bus.advance(notice, 10)
    bus.on_bus = True
    notice.poll(False)
    bus.advance(notice, 100, connected=True)
    assert "Unplug and replug" not in caplog.text


def test_a_new_stuck_episode_is_reported_again(caplog):
    bus = Bus(); notice = bus.notice()
    notice.poll(False)
    bus.advance(notice, 10)
    bus.advance(notice, 1, connected=True)  # got fixed
    notice.poll(False)
    bus.advance(notice, 10)
    assert caplog.text.count("Unplug and replug") == 2


def test_serve_polls_the_stuck_notice_while_waiting():
    polled = []

    class Spy:
        def poll(self, connected):
            polled.append(connected)

    log = []
    with pytest.raises(KeyboardInterrupt):
        serve(ConfigWatcher(None), LAYOUT, Audio(), queue.Queue(),
              lambda: Session(log), scripted([False, False, True, True]), stuck=Spy())
    assert polled[:3] == [False, False, True]
