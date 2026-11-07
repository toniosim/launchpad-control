"""Main loop: events in -> state -> frame -> diffed LED updates out."""
from __future__ import annotations

import argparse
import logging
import queue
import signal
from dataclasses import dataclass
from typing import Protocol

from . import grid
from .device import Device
from .events import AudioChanged, Event, PadPress
from .integrations.pipewire import AudioState
from .widgets import Fader

log = logging.getLogger(__name__)


# ---- Layout (hardcoded until there's a config file) -------------------------

@dataclass(frozen=True)
class AppFader:
    name: str
    patterns: tuple[str, ...]  # matched against the stream's app name / binary
    fader: Fader


MIC_MUTE_PAD = grid.idx(1, 8)  # bottom-right
MASTER_FADER = Fader(col=1, color=grid.GREEN)
APP_FADERS = (
    AppFader("firefox", ("firefox",), Fader(col=2, color=grid.ORANGE)),
    AppFader("discord", ("discord",), Fader(col=3, color=grid.PURPLE)),
)


# ---- App ---------------------------------------------------------------------

class AudioControl(Protocol):
    def toggle_mic_mute(self) -> None: ...
    def set_master_volume(self, level: float) -> None: ...
    def set_app_volume(self, name: str, level: float) -> None: ...


class App:
    def __init__(self, device: Device, audio: AudioControl) -> None:
        self.device = device
        self.audio = audio
        self.state = AudioState()
        self._differ = grid.FrameDiffer()

    def handle(self, event: Event) -> None:
        # Presses only act on the system. LEDs change when PipeWire reports the new state.
        if isinstance(event, PadPress):
            self._on_press(event.idx)
        elif isinstance(event, AudioChanged):
            self.state = event.state

    def _on_press(self, idx: int) -> None:
        if idx == MIC_MUTE_PAD:
            self.audio.toggle_mic_mute()
            return
        if MASTER_FADER.contains(idx):
            self.audio.set_master_volume(
                MASTER_FADER.level_for_press(idx, self.state.master_volume))
            return
        for app in APP_FADERS:
            if app.fader.contains(idx):
                current = self.state.app_volumes.get(app.name)
                if current is None:
                    log.info("%s has no audio stream right now", app.name)
                    return
                self.audio.set_app_volume(app.name, app.fader.level_for_press(idx, current))
                return

    def render(self) -> grid.Frame:
        s = self.state
        frame: grid.Frame = {}
        frame.update(MASTER_FADER.render(s.master_volume))
        for app in APP_FADERS:
            frame.update(app.fader.render(s.app_volumes.get(app.name)))
        if s.mic_muted is not None:
            frame[MIC_MUTE_PAD] = grid.RED if s.mic_muted else grid.DIM_GREEN
        return frame

    def refresh(self) -> None:
        self.device.set_leds(self._differ.diff(self.render()))

    def step(self, event: Event) -> None:
        self.handle(event)
        self.refresh()


def run() -> None:
    from .device import LaunchpadDevice
    from .integrations.pipewire import PipeWire

    events: queue.Queue[Event] = queue.Queue()
    audio = PipeWire(apps={a.name: a.patterns for a in APP_FADERS})

    # systemd sends SIGTERM; treat it like Ctrl+C so the device gets restored.
    def _sigterm(*_: object) -> None:
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _sigterm)

    with LaunchpadDevice() as dev:
        app = App(dev, audio)
        dev.set_event_handler(events.put)
        audio.watch(lambda s: events.put(AudioChanged(s)))  # also sends the initial state

        log.info("Running. Ctrl+C to quit.")
        try:
            while True:
                try:
                    event = events.get(timeout=0.5)
                except queue.Empty:
                    continue
                log.debug("%s", event)
                app.step(event)
        except KeyboardInterrupt:
            pass
        finally:
            audio.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="log every event")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run()


if __name__ == "__main__":
    main()
