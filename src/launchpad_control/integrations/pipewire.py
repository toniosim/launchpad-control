"""PipeWire: mic mute, master volume, per-app volume, and a watcher that reports changes.

Every change, wherever it came from (Launchpad, keyboard, desktop panel, pavucontrol),
shows up as a fresh AudioState snapshot via `watch()`.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

log = logging.getLogger(__name__)

Runner = Callable[[list[str]], str]

# `pactl subscribe` lines look like: Event 'change' on source #4353
_EVENT_RE = re.compile(r"Event '(\w+)' on ([\w-]+)")
# source = mic mute, sink = master volume, sink-input = app streams, server = default device changed
_RELEVANT = {"source", "sink", "sink-input", "server"}
# Stream properties checked against an app's match patterns (case-insensitive substring).
_MATCH_PROPS = ("application.name", "application.process.binary", "application.id", "node.name")
_DEBOUNCE_S = 0.03
_FULL_VOLUME = 65536  # PulseAudio volume units for 100%


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=5).stdout


def is_relevant_event(line: str) -> bool:
    m = _EVENT_RE.search(line)
    return bool(m) and m.group(2) in _RELEVANT


@dataclass(frozen=True)
class SinkInput:
    index: int
    level: float  # 1.0 = 100%
    props: Mapping[str, str] = field(default_factory=dict)

    def matches(self, patterns: Sequence[str]) -> bool:
        values = [str(self.props.get(k, "")).lower() for k in _MATCH_PROPS]
        return any(p.lower() in v for p in patterns for v in values)


@dataclass(frozen=True)
class AudioState:
    """None anywhere means "unknown / not available" (e.g. app has no stream right now)."""
    mic_muted: bool | None = None
    master_volume: float | None = None
    app_volumes: Mapping[str, float | None] = field(default_factory=dict)


class PipeWire:
    def __init__(self, apps: Mapping[str, Sequence[str]] | None = None,
                 run: Runner = _run) -> None:
        """apps: app name -> match patterns, e.g. {"firefox": ["firefox"]}."""
        self.apps = dict(apps or {})
        self._run = run
        self._stop = threading.Event()
        self._dirty = threading.Event()
        self._proc: subprocess.Popen[str] | None = None

    # ---- mic mute -------------------------------------------------------
    def is_mic_muted(self) -> bool | None:
        out = self._try(["pactl", "get-source-mute", "@DEFAULT_SOURCE@"])
        if out is None:
            return None
        value = out.strip().rpartition(":")[2].strip()  # "Mute: yes" -> "yes"
        if value in ("yes", "no"):
            return value == "yes"
        log.warning("Unexpected pactl output: %r", out)
        return None

    def toggle_mic_mute(self) -> None:
        self._try(["wpctl", "set-mute", "@DEFAULT_SOURCE@", "toggle"])

    # ---- master volume --------------------------------------------------
    def master_volume(self) -> float | None:
        out = self._try(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
        m = re.search(r"Volume:\s*([\d.]+)", out or "")
        return float(m.group(1)) if m else None

    def set_master_volume(self, level: float) -> None:
        self._try(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{_clamp01(level):.3f}"])

    # ---- per-app volume -------------------------------------------------
    def sink_inputs(self) -> list[SinkInput] | None:
        out = self._try(["pactl", "-f", "json", "list", "sink-inputs"])
        if out is None:
            return None
        try:
            return parse_sink_inputs(out)
        except (ValueError, KeyError, TypeError) as e:
            log.warning("Couldn't parse sink-inputs: %s", e)
            return None

    def app_volumes(self, streams: list[SinkInput] | None = None) -> dict[str, float | None]:
        """Loudest matching stream per app, or None if the app has no stream."""
        streams = self.sink_inputs() if streams is None else streams
        result: dict[str, float | None] = {}
        for name, patterns in self.apps.items():
            levels = [s.level for s in streams or [] if s.matches(patterns)]
            result[name] = max(levels) if levels else None
        return result

    def set_app_volume(self, name: str, level: float) -> None:
        """Set every stream belonging to the app (e.g. each Firefox tab playing audio)."""
        patterns = self.apps[name]
        pct = f"{round(_clamp01(level) * 100)}%"
        for s in self.sink_inputs() or []:
            if s.matches(patterns):
                self._try(["pactl", "set-sink-input-volume", str(s.index), pct])

    # ---- snapshot + watching --------------------------------------------
    def snapshot(self) -> AudioState:
        return AudioState(self.is_mic_muted(), self.master_volume(), self.app_volumes())

    def watch(self, on_change: Callable[[AudioState], None]) -> None:
        """Call on_change(snapshot) at start and whenever audio state might have changed.

        Bursts of PipeWire events are coalesced. `pactl subscribe` is restarted if
        it dies (e.g. PipeWire restarted). Callers should tolerate repeated values.
        """
        threading.Thread(target=self._read_events, daemon=True, name="pw-events").start()
        threading.Thread(target=self._refresh_loop, args=(on_change,), daemon=True,
                         name="pw-refresh").start()

    def stop(self) -> None:
        self._stop.set()
        self._dirty.set()
        if self._proc is not None:
            self._proc.terminate()

    def _read_events(self) -> None:
        while not self._stop.is_set():
            try:
                self._proc = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE,
                                              stderr=subprocess.DEVNULL, text=True)
                self._dirty.set()  # re-read on (re)connect
                assert self._proc.stdout is not None
                for line in self._proc.stdout:
                    if is_relevant_event(line):
                        self._dirty.set()
            except OSError as e:
                log.warning("pactl subscribe failed: %s", e)
            if not self._stop.is_set():
                log.info("pactl subscribe exited; restarting in 2s")
                self._stop.wait(2)

    def _refresh_loop(self, on_change: Callable[[AudioState], None]) -> None:
        while not self._stop.is_set():
            self._dirty.wait()
            self._stop.wait(_DEBOUNCE_S)
            self._dirty.clear()
            if not self._stop.is_set():
                on_change(self.snapshot())

    def _try(self, cmd: list[str]) -> str | None:
        try:
            return self._run(cmd)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("%s failed: %s", " ".join(cmd[:3]), e)
            return None


def parse_sink_inputs(json_text: str) -> list[SinkInput]:
    streams = []
    for s in json.loads(json_text):
        channels = [ch["value"] for ch in s["volume"].values()]
        level = max(channels) / _FULL_VOLUME if channels else 0.0
        streams.append(SinkInput(int(s["index"]), level, s.get("properties", {})))
    return streams


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))
