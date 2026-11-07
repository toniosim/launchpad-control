import os
import time
import tomllib

import pytest

from launchpad_control import config, grid
from launchpad_control.app import ConfigWatcher
from launchpad_control.config import ConfigError, parse


def test_default_config_loads():
    layout = config.load(None)
    assert layout.mic_mute_pad == 18
    assert layout.master.col == 1
    assert [a.name for a in layout.apps] == ["firefox", "discord"]


def test_empty_config_is_valid():
    assert parse({}) == config.Layout()


def test_rgb_and_named_colours():
    layout = parse({"master": {"column": 1, "color": [1, 2, 3]},
                    "apps": [{"name": "x", "column": 2, "color": "Purple"}]})
    assert layout.master.color == (1, 2, 3)
    assert layout.apps[0].fader.color == grid.PURPLE


def test_match_defaults_to_name():
    layout = parse({"apps": [{"name": "spotify", "column": 4, "color": "green"}]})
    assert layout.apps[0].patterns == ("spotify",)


@pytest.mark.parametrize("data, message", [
    ({"mic_mute": {"pad": [0, 1]}}, "1-8"),
    ({"mic_mute": {"pad": 18}}, "[row, col]"),
    ({"master": {"column": 9, "color": "red"}}, "column must be 1-8"),
    ({"master": {"column": 1}}, "missing 'color'"),
    ({"master": {"column": 1, "color": "mauve"}}, "unknown colour"),
    ({"master": {"column": 1, "color": [200, 0, 0]}}, "0-127"),
    ({"master": {"column": 1, "colour": "red"}}, "unknown key(s) colour"),
    ({"masters": {}}, "unknown key(s) masters"),
    ({"apps": [{"column": 2, "color": "red"}]}, "missing 'name'"),
    ({"apps": [{"name": "a", "match": [], "column": 2, "color": "red"}]}, "match must be"),
    ({"apps": [{"name": "a", "column": 2, "color": "red"},
               {"name": "a", "column": 3, "color": "red"}]}, "duplicate app name"),
    ({"master": {"column": 2, "color": "red"},
      "apps": [{"name": "a", "column": 2, "color": "red"}]}, "column 2 is used by both"),
    ({"mic_mute": {"pad": [4, 1]}, "master": {"column": 1, "color": "red"}},
     "belongs to master"),
])
def test_validation_errors(data, message):
    with pytest.raises(ConfigError, match=None) as e:
        parse(data)
    assert message in str(e.value)


def test_load_reports_file_and_toml_errors(tmp_path):
    bad = tmp_path / "c.toml"
    bad.write_text("[master\n")
    with pytest.raises(ConfigError, match=str(bad)):
        config.load(bad)


def test_resolve_prefers_explicit_then_user(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config.resolve_path(None) is None  # no user file -> packaged default
    user = tmp_path / "launchpad-control" / "config.toml"
    user.parent.mkdir()
    user.write_text("")
    assert config.resolve_path(None) == user
    with pytest.raises(ConfigError, match="not found"):
        config.resolve_path(tmp_path / "nope.toml")


def test_watcher_reloads_on_change(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[mic_mute]\npad = [1, 8]\n')
    w = ConfigWatcher(path)
    assert w.initial().mic_mute_pad == 18
    assert w.check() is None  # unchanged

    path.write_text('[mic_mute]\npad = [8, 8]\n')
    os.utime(path, ns=(time.time_ns(), time.time_ns() + 1_000_000))
    assert w.check().mic_mute_pad == 88

    path.write_text('[mic_mute]\npad = "oops"\n')
    os.utime(path, ns=(time.time_ns(), time.time_ns() + 2_000_000))
    with pytest.raises(ConfigError):
        w.check()
    assert w.check() is None  # bad file isn't retried until it changes again


def test_active_app_section():
    layout = parse({"active_app": {"column": 4, "color": "cyan", "ignore": ["sd"]}})
    assert layout.active_app.fader.col == 4
    assert layout.active_app.ignore == ("sd",)
    assert parse({"active_app": {"column": 4, "color": "cyan"}}).active_app.ignore == ()


def test_active_app_validation():
    with pytest.raises(ConfigError, match="ignore must be"):
        parse({"active_app": {"column": 4, "color": "cyan", "ignore": "sd"}})
    with pytest.raises(ConfigError, match="column 2 is used by both app 'a' and active_app"):
        parse({"apps": [{"name": "a", "column": 2, "color": "red"}],
               "active_app": {"column": 2, "color": "cyan"}})
