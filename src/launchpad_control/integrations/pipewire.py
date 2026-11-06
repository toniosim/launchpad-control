"""PipeWire mic mute: toggle it, read it, and watch for changes from anywhere."""
from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

Runner = Callable[[list[str]], str]

# `pactl subscribe` lines look like: Event 'change' on source #4353
_EVENT_RE = re.compile(r"Event '(\w+)' on ([\w-]+)")
# Source changes cover mute toggles; server changes cover a new default mic.
_RELEVANT = {"source", "server"}


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=5).stdout


def is_relevant_event(line: str) -> bool:
    m = _EVENT_RE.search(line)
    return bool(m) and m.group(2) in _RELEVANT


class MicMute:
    """System-wide mute of the default source. Affects every app, not just Discord."""

    def __init__(self, run: Runner = _run) -> None:
        self._run = run
        self._stop = threading.Event()
        self._proc: subprocess.Popen[str] | None = None

    def is_muted(self) -> bool | None:
        """True/False, or None if PipeWire couldn't be queried."""
        try:
            out = self._run(["pactl", "get-source-mute", "@DEFAULT_SOURCE@"])
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("Couldn't read mic mute: %s", e)
            return None
        value = out.strip().rpartition(":")[2].strip()  # "Mute: yes" -> "yes"
        if value == "yes":
            return True
        if value == "no":
            return False
        log.warning("Unexpected pactl output: %r", out)
        return None

    def toggle(self) -> None:
        try:
            self._run(["wpctl", "set-mute", "@DEFAULT_SOURCE@", "toggle"])
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("Couldn't toggle mic mute: %s", e)

    def watch(self, on_change: Callable[[bool], None]) -> threading.Thread:
        """Call on_change(muted) whenever the mute state might have changed.

        Runs `pactl subscribe` in a daemon thread and restarts it if it dies
        (e.g. PipeWire restarted). Callers should tolerate repeated values.
        """
        t = threading.Thread(target=self._watch_loop, args=(on_change,), daemon=True,
                             name="pipewire-watch")
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
        if self._proc is not None:
            self._proc.terminate()

    def _watch_loop(self, on_change: Callable[[bool], None]) -> None:
        while not self._stop.is_set():
            try:
                self._proc = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE,
                                              stderr=subprocess.DEVNULL, text=True)
                # Re-read once on (re)connect in case something changed while we weren't looking.
                self._report(on_change)
                assert self._proc.stdout is not None
                for line in self._proc.stdout:
                    if is_relevant_event(line):
                        self._report(on_change)
            except OSError as e:
                log.warning("pactl subscribe failed: %s", e)
            if not self._stop.is_set():
                log.info("pactl subscribe exited; restarting in 2s")
                self._stop.wait(2)

    def _report(self, on_change: Callable[[bool], None]) -> None:
        muted = self.is_muted()
        if muted is not None:
            on_change(muted)
