import subprocess

from launchpad_control.integrations.media import Media


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
