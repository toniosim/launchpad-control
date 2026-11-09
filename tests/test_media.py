import subprocess

import pytest

from launchpad_control.integrations import media as media_module
from launchpad_control.integrations.media import Media, MediaState, parse_status


def result(code=0, err=""):
    return subprocess.CompletedProcess([], code, "", err)


def test_commands():
    seen = []
    m = Media(lambda cmd: seen.append(cmd) or result())
    m.previous()
    m.play_pause()
    m.next()
    assert seen == [["playerctl", "previous"], ["playerctl", "play-pause"],
                    ["playerctl", "next"]]


def test_no_player_is_not_an_error(caplog):
    caplog.set_level("INFO")
    Media(lambda cmd: result(1, "No players found")).play_pause()
    assert "No players found" in caplog.text
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_missing_playerctl_warns_and_does_not_raise(caplog):
    def run(cmd):
        raise FileNotFoundError("playerctl")
    Media(run).next()
    assert "isn't installed" in caplog.text


def test_timeout_does_not_raise(caplog):
    def run(cmd):
        raise subprocess.TimeoutExpired(cmd, 5)
    Media(run).next()
    assert "failed" in caplog.text


# ---- play state ----

@pytest.mark.parametrize("line, status", [
    ("Playing\n", "Playing"), ("Paused\n", "Paused"), ("Stopped\n", "Stopped"),
    ("\n", None),  # playerctl prints an empty line when the player goes away
    ("", None),
])
def test_parse_status(line, status):
    assert parse_status(line) == MediaState(status)


def test_state_flags():
    assert not MediaState().available and not MediaState().playing
    assert MediaState("Paused").available and not MediaState("Paused").playing
    assert MediaState("Playing").playing


class FakePopen:
    """Stands in for `playerctl --follow status`: emits the lines, then stops the watcher."""

    def __init__(self, media, lines):
        self.stdout = self._lines(media, lines)
        self.reaped = False

    def wait(self, timeout=None):
        self.reaped = True

    @staticmethod
    def _lines(media, lines):
        yield from lines
        media._stop.set()  # follower exits after this, so the watch loop ends


def run_follower(monkeypatch, lines):
    m, seen = Media(), []
    monkeypatch.setattr(media_module.subprocess, "Popen",
                        lambda cmd, **kw: (seen.append(cmd), FakePopen(m, lines))[1])
    states = []
    m._follow(states.append)
    assert m._proc.reaped  # no zombie left behind
    return states, seen


def test_follower_reports_each_status_line(monkeypatch):
    states, cmds = run_follower(monkeypatch, ["Playing\n", "Paused\n", "\n"])
    assert cmds == [["playerctl", "--follow", "status"]]
    assert states == [MediaState(), MediaState("Playing"), MediaState("Paused"), MediaState()]


def test_follower_survives_missing_playerctl(monkeypatch, caplog):
    def popen(cmd, **kw):
        raise FileNotFoundError("playerctl")
    monkeypatch.setattr(media_module.subprocess, "Popen", popen)
    states = []
    Media()._follow(states.append)  # returns instead of retrying forever
    assert states == [MediaState()]
    assert "isn't installed" in caplog.text
