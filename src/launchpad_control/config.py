"""Layout config: where things live on the grid, what colour they are, which apps to control.

Lookup order: --config PATH, then ~/.config/launchpad-control/config.toml
($XDG_CONFIG_HOME respected), then the packaged default_config.toml.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from . import grid
from .grid import RGB
from .widgets import Fader

NAMED_COLORS: dict[str, RGB] = {
    "red": grid.RED,
    "green": grid.GREEN,
    "blue": (0, 0, 127),
    "orange": grid.ORANGE,
    "purple": grid.PURPLE,
    "yellow": (127, 100, 0),
    "cyan": (0, 100, 127),
    "pink": (127, 20, 60),
    "white": (127, 127, 127),
}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class AppFader:
    name: str
    patterns: tuple[str, ...]  # matched against the stream's app name / binary
    fader: Fader


@dataclass(frozen=True)
class ActiveAppFader:
    """Controls whatever else is playing (e.g. a game): the newest audio stream
    not claimed by an [[apps]] fader and not in `ignore`."""
    fader: Fader
    ignore: tuple[str, ...] = ()


@dataclass(frozen=True)
class Layout:
    mic_mute_pad: int | None = None
    master: Fader | None = None
    apps: tuple[AppFader, ...] = ()
    active_app: ActiveAppFader | None = None

    def faders(self) -> list[Fader]:
        return (([self.master] if self.master else []) + [a.fader for a in self.apps]
                + ([self.active_app.fader] if self.active_app else []))


# ---- locating files ------------------------------------------------------------

def user_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "launchpad-control" / "config.toml"


def default_config_text() -> str:
    return resources.files("launchpad_control").joinpath("default_config.toml").read_text()


def resolve_path(explicit: Path | None) -> Path | None:
    """The file to load, or None for the packaged default."""
    if explicit is not None:
        if not explicit.is_file():
            raise ConfigError(f"Config file not found: {explicit}")
        return explicit
    user = user_config_path()
    return user if user.is_file() else None


def load(path: Path | None) -> Layout:
    """Load and validate a config file (None = packaged default)."""
    where = str(path) if path else "default config"
    try:
        text = path.read_text() if path else default_config_text()
        data = tomllib.loads(text)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"{where}: {e}") from e
    try:
        return parse(data)
    except ConfigError as e:
        raise ConfigError(f"{where}: {e}") from None


# ---- parsing + validation ------------------------------------------------------

def parse(data: dict[str, Any]) -> Layout:
    _no_unknown_keys(data, {"mic_mute", "master", "apps", "active_app"}, "top level")

    mic_pad = None
    if "mic_mute" in data:
        section = _table(data["mic_mute"], "[mic_mute]")
        _no_unknown_keys(section, {"pad"}, "[mic_mute]")
        mic_pad = _pad(_required(section, "pad", "[mic_mute]"), "[mic_mute] pad")

    master = None
    if "master" in data:
        master = _fader(_table(data["master"], "[master]"), "[master]", set())

    apps = []
    raw_apps = data.get("apps", [])
    if not isinstance(raw_apps, list):
        raise ConfigError("apps must be a list of [[apps]] tables")
    for n, raw in enumerate(raw_apps, 1):
        where = f"[[apps]] #{n}"
        section = _table(raw, where)
        name = _required(section, "name", where)
        if not isinstance(name, str) or not name:
            raise ConfigError(f"{where}: name must be a non-empty string")
        where = f"[[apps]] '{name}'"
        patterns = section.get("match", [name])
        if (not isinstance(patterns, list) or not patterns
                or not all(isinstance(p, str) and p for p in patterns)):
            raise ConfigError(f"{where}: match must be a non-empty list of strings")
        fader = _fader(section, where, {"name", "match"})
        apps.append(AppFader(name, tuple(patterns), fader))

    active = None
    if "active_app" in data:
        section = _table(data["active_app"], "[active_app]")
        ignore = section.get("ignore", [])
        if not isinstance(ignore, list) or not all(isinstance(p, str) and p for p in ignore):
            raise ConfigError("[active_app]: ignore must be a list of non-empty strings")
        active = ActiveAppFader(_fader(section, "[active_app]", {"ignore"}), tuple(ignore))

    layout = Layout(mic_pad, master, tuple(apps), active)
    _check_overlaps(layout)
    return layout


def _fader(section: dict[str, Any], where: str, extra_keys: set[str]) -> Fader:
    _no_unknown_keys(section, {"column", "color"} | extra_keys, where)
    col = _required(section, "column", where)
    if not isinstance(col, int) or isinstance(col, bool) or not 1 <= col <= 8:
        raise ConfigError(f"{where}: column must be 1-8, got {col!r}")
    color = _color(_required(section, "color", where), f"{where} color")
    return Fader(col=col, color=color)


def _check_overlaps(layout: Layout) -> None:
    names = [a.name for a in layout.apps]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ConfigError(f"duplicate app name(s): {', '.join(sorted(dupes))}")

    owners: dict[int, str] = {}
    labelled = ([("master", layout.master)] if layout.master else []) + \
        [(f"app '{a.name}'", a.fader) for a in layout.apps] + \
        ([("active_app", layout.active_app.fader)] if layout.active_app else [])
    for label, fader in labelled:
        if fader.col in owners:
            raise ConfigError(f"column {fader.col} is used by both {owners[fader.col]} and {label}")
        owners[fader.col] = label

    if layout.mic_mute_pad is not None and layout.mic_mute_pad % 10 in owners:
        col = layout.mic_mute_pad % 10
        raise ConfigError(f"mic_mute pad is in column {col}, which belongs to {owners[col]}")


def _pad(value: Any, where: str) -> int:
    if (not isinstance(value, list) or len(value) != 2
            or not all(isinstance(v, int) and not isinstance(v, bool) for v in value)):
        raise ConfigError(f"{where} must be [row, col], got {value!r}")
    try:
        return grid.idx(*value)
    except ValueError:
        raise ConfigError(f"{where}: row and col must be 1-8, got {value!r}") from None


def _color(value: Any, where: str) -> RGB:
    if isinstance(value, str):
        if value.lower() not in NAMED_COLORS:
            raise ConfigError(f"{where}: unknown colour {value!r} "
                              f"(known: {', '.join(NAMED_COLORS)}; or use [r, g, b])")
        return NAMED_COLORS[value.lower()]
    if (isinstance(value, list) and len(value) == 3
            and all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 127
                    for v in value)):
        return (value[0], value[1], value[2])
    raise ConfigError(f"{where} must be a colour name or [r, g, b] with 0-127, got {value!r}")


def _table(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{where} must be a table")
    return value


def _required(section: dict[str, Any], key: str, where: str) -> Any:
    if key not in section:
        raise ConfigError(f"{where}: missing '{key}'")
    return section[key]


def _no_unknown_keys(section: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = set(section) - allowed
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(sorted(unknown))} "
                          f"(allowed: {', '.join(sorted(allowed))})")
