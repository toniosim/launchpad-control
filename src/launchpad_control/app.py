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
from .events import Event, MicMuteChanged, PadPress

log = logging.getLogger(__name__)

MIC_MUTE_PAD = grid.idx(8, 1)  # top-left pad (81) until there's a config file


class MicControl(Protocol):
    def toggle(self) -> None: ...


@dataclass
class State:
    mic_muted: bool | None = None  # None = unknown, pad stays off


class App:
    def __init__(self, device: Device, mic: MicControl) -> None:
        self.device = device
        self.mic = mic
        self.state = State()
        self._differ = grid.FrameDiffer()

    def handle(self, event: Event) -> None:
        if isinstance(event, PadPress) and event.idx == MIC_MUTE_PAD:
            # Don't touch the LED here: it changes when PipeWire reports the new state.
            self.mic.toggle()
        elif isinstance(event, MicMuteChanged):
            self.state.mic_muted = event.muted

    def render(self) -> grid.Frame:
        frame: grid.Frame = {}
        if self.state.mic_muted is not None:
            frame[MIC_MUTE_PAD] = grid.RED if self.state.mic_muted else grid.DIM_GREEN
        return frame

    def refresh(self) -> None:
        self.device.set_leds(self._differ.diff(self.render()))

    def step(self, event: Event) -> None:
        self.handle(event)
        self.refresh()


def run() -> None:
    from .device import LaunchpadDevice
    from .integrations.pipewire import MicMute

    events: queue.Queue[Event] = queue.Queue()
    mic = MicMute()

    # systemd sends SIGTERM; treat it like Ctrl+C so the device gets restored.
    def _sigterm(*_: object) -> None:
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _sigterm)

    with LaunchpadDevice() as dev:
        app = App(dev, mic)
        dev.set_event_handler(events.put)

        muted = mic.is_muted()
        if muted is not None:
            app.step(MicMuteChanged(muted))
        mic.watch(lambda m: events.put(MicMuteChanged(m)))

        log.info("Running. Mic mute pad = %d. Ctrl+C to quit.", MIC_MUTE_PAD)
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
            mic.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="log every event")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run()


if __name__ == "__main__":
    main()
