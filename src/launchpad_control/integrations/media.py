"""Media transport (previous / play-pause / next) and play state, via playerctl (MPRIS).

playerctl picks the player itself; with `playerctld` running that is the most recently
active one. Actions are fire-and-forget; the play state comes from `watch()`, which
follows `playerctl --follow status` (event driven, no polling).
"""
from __future__ import annotations

import logging
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

# (returncode, stderr) of one playerctl invocation
Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


_RESTART_DELAY_S = 2.0


@dataclass(frozen=True)
class MediaState:
    """status is playerctl's word for it ("Playing", "Paused", "Stopped"); None = no player."""
    status: str | None = None

    @property
    def available(self) -> bool:
        return self.status is not None

    @property
    def playing(self) -> bool:
        return self.status == "Playing"


def parse_status(line: str) -> MediaState:
    """One line of `playerctl --follow status`. An empty line means the player went away."""
    return MediaState(line.strip() or None)


def _run(cmd: list[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(cmd, capture_output=True, text=True, timeout=5)


def _reap(proc: "subprocess.Popen[str]") -> None:
    """Collect the exited follower so it doesn't linger as a zombie."""
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


class Media:
    def __init__(self, run: Runner = _run) -> None:
        self._run = run
        self._stop = threading.Event()
        self._proc: subprocess.Popen[str] | None = None

    def previous(self) -> None:
        self._playerctl("previous")

    def play_pause(self) -> None:
        self._playerctl("play-pause")

    def next(self) -> None:
        self._playerctl("next")

    # ---- play state -----------------------------------------------------
    def watch(self, on_change: Callable[[MediaState], None]) -> None:
        """Call on_change(state) whenever the player starts, pauses, stops or goes away.

        Reports "no player" whenever the follower (re)starts, so a stale state can't
        outlive it. Callers should tolerate repeated values.
        """
        threading.Thread(target=self._follow, args=(on_change,), daemon=True,
                         name="media-follow").start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc is not None:
            self._proc.terminate()

    def _follow(self, on_change: Callable[[MediaState], None]) -> None:
        while not self._stop.is_set():
            on_change(MediaState())
            try:
                proc = self._proc = subprocess.Popen(
                    ["playerctl", "--follow", "status"], stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True)
                assert proc.stdout is not None
                try:
                    for line in proc.stdout:
                        on_change(parse_status(line))
                finally:
                    _reap(proc)
            except FileNotFoundError:
                log.warning("playerctl isn't installed (sudo apt install playerctl)")
                return  # retrying can't help
            except OSError as e:
                log.warning("playerctl --follow failed: %s", e)
            if not self._stop.is_set():
                log.info("playerctl --follow exited; restarting in %.0fs", _RESTART_DELAY_S)
                self._stop.wait(_RESTART_DELAY_S)

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
