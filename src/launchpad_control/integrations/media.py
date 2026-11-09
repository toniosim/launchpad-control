"""Media transport (previous / play-pause / next) for whatever player is active, via playerctl (MPRIS).

playerctl picks the player itself; with `playerctld` running that is the most recently
active one. Fire-and-forget: there is no state to report back.
"""
from __future__ import annotations

import logging
import subprocess
from typing import Callable

log = logging.getLogger(__name__)

# (returncode, stderr) of one playerctl invocation
Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _run(cmd: list[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(cmd, capture_output=True, text=True, timeout=5)


class Media:
    def __init__(self, run: Runner = _run) -> None:
        self._run = run

    def previous(self) -> None:
        self._playerctl("previous")

    def play_pause(self) -> None:
        self._playerctl("play-pause")

    def next(self) -> None:
        self._playerctl("next")

    def _playerctl(self, command: str) -> None:
        try:
            result = self._run(["playerctl", command])
        except FileNotFoundError:
            log.warning("playerctl isn't installed (sudo apt install playerctl)")
            return
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("playerctl %s failed: %s", command, e)
            return
        if result.returncode != 0:
            # Most often "No players found": nothing is playing, which isn't an error.
            log.info("playerctl %s: %s", command, result.stderr.strip() or f"exit {result.returncode}")
