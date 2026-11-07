"""Main loop: events in -> state -> frame -> diffed LED updates out."""
from __future__ import annotations

import argparse
import logging
import queue
import signal
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Callable, ContextManager, Protocol

from . import config, grid
from .config import ConfigError, Layout
from .device import Device
from .events import AudioChanged, Event, PadPress
from .integrations.pipewire import AudioState

log = logging.getLogger(__name__)

CONFIG_POLL_S = 1.0
DEVICE_POLL_S = 2.0  # how often to check the Launchpad is still (or again) plugged in


class AudioControl(Protocol):
    def toggle_mic_mute(self) -> None: ...
    def set_master_volume(self, level: float) -> None: ...
    def set_app_volume(self, name: str, level: float) -> None: ...
    def set_active_volume(self, level: float) -> None: ...


class App:
    def __init__(self, device: Device, audio: AudioControl, layout: Layout) -> None:
        self.device = device
        self.audio = audio
        self.layout = layout
        self.state = AudioState()
        self._differ = grid.FrameDiffer()

    def handle(self, event: Event) -> None:
        # Presses only act on the system. LEDs change when PipeWire reports the new state.
        if isinstance(event, PadPress):
            self._on_press(event.idx)
        elif isinstance(event, AudioChanged):
            if event.state.active_app != self.state.active_app and self.layout.active_app:
                log.info("Active-app fader now controls: %s", event.state.active_app or "nothing")
            self.state = event.state

    def _on_press(self, idx: int) -> None:
        layout = self.layout
        if idx == layout.mic_mute_pad:
            self.audio.toggle_mic_mute()
            return
        if layout.master and layout.master.contains(idx):
            self.audio.set_master_volume(
                layout.master.level_for_press(idx, self.state.master_volume))
            return
        for app in layout.apps:
            if app.fader.contains(idx):
                current = self.state.app_volumes.get(app.name)
                if current is None:
                    log.info("%s has no audio stream right now", app.name)
                    return
                self.audio.set_app_volume(app.name, app.fader.level_for_press(idx, current))
                return
        active = layout.active_app
        if active and active.fader.contains(idx):
            current = self.state.active_volume
            if current is None:
                log.info("No other app is playing audio right now")
                return
            self.audio.set_active_volume(active.fader.level_for_press(idx, current))

    def render(self) -> grid.Frame:
        s, layout = self.state, self.layout
        frame: grid.Frame = {}
        if layout.master:
            frame.update(layout.master.render(s.master_volume))
        for app in layout.apps:
            frame.update(app.fader.render(s.app_volumes.get(app.name)))
        if layout.active_app:
            frame.update(layout.active_app.fader.render(s.active_volume))
        if layout.mic_mute_pad is not None and s.mic_muted is not None:
            frame[layout.mic_mute_pad] = grid.RED if s.mic_muted else grid.DIM_GREEN
        return frame

    def refresh(self) -> None:
        self.device.set_leds(self._differ.diff(self.render()))

    def step(self, event: Event) -> None:
        self.handle(event)
        self.refresh()


class ConfigWatcher:
    """Notices when the config file changes (or a user config appears) and reloads it."""

    def __init__(self, explicit: Path | None) -> None:
        self.explicit = explicit
        self.path = config.resolve_path(explicit)
        self._mtime = self._stat(self.path)

    def initial(self) -> Layout:
        return config.load(self.path)

    def check(self) -> Layout | None:
        """New layout if the file changed, else None. Raises ConfigError if it's invalid."""
        try:
            path = config.resolve_path(self.explicit)
        except ConfigError:
            return None  # explicit file briefly missing (e.g. editor saving): keep current
        mtime = self._stat(path)
        if (path, mtime) == (self.path, self._mtime):
            return None
        self.path, self._mtime = path, mtime
        return config.load(path)

    @staticmethod
    def _stat(path: Path | None) -> int | None:
        try:
            return path.stat().st_mtime_ns if path else None
        except OSError:
            return None


def run(config_path: Path | None) -> None:
    from .device import LaunchpadDevice, launchpad_present
    from .integrations.pipewire import PipeWire

    watcher = ConfigWatcher(config_path)
    layout = watcher.initial()
    log.info("Config: %s", watcher.path or "packaged default")

    events: queue.Queue[Event] = queue.Queue()
    audio = PipeWire(*_audio_targets(layout))

    # systemd sends SIGTERM; treat it like Ctrl+C so the device gets restored.
    def _sigterm(*_: object) -> None:
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _sigterm)

    audio.watch(lambda s: events.put(AudioChanged(s)))  # also sends the initial state
    log.info("Running. Ctrl+C to quit.")
    try:
        serve(watcher, layout, audio, events, LaunchpadDevice, launchpad_present)
    except KeyboardInterrupt:
        pass
    finally:
        audio.stop()


def serve(watcher: ConfigWatcher, layout: Layout, audio, events: "queue.Queue[Event]",
          open_device: Callable[[], ContextManager[Device]],
          present: Callable[[], bool]) -> None:
    """Run until interrupted, surviving the Launchpad being absent, unplugged or replugged.

    The audio state and layout carry over between device sessions, so a replugged
    Launchpad is repainted from the current state straight away.
    """
    state = AudioState()
    waiting_logged = False
    while True:
        stack = ExitStack()
        try:
            if not present():
                raise LookupError("not connected")
            dev = stack.enter_context(open_device())
        except Exception as e:
            if not waiting_logged:
                log.info("Waiting for the Launchpad (%s)", e)
                waiting_logged = True
            state = _idle(events, state)
            continue
        waiting_logged = False
        with stack:
            log.info("Launchpad connected")
            app = App(dev, audio, layout)
            app.state = state
            dev.set_event_handler(events.put)
            try:
                app.refresh()
                _session(app, watcher, audio, events, present)
            finally:
                layout, state = app.layout, app.state


def _idle(events: "queue.Queue[Event]", state: AudioState) -> AudioState:
    """Wait for the device to show up, still tracking audio state so the queue can't grow."""
    try:
        event = events.get(timeout=DEVICE_POLL_S)
    except queue.Empty:
        return state
    return event.state if isinstance(event, AudioChanged) else state


def _session(app: App, watcher: ConfigWatcher, audio, events: "queue.Queue[Event]",
             present: Callable[[], bool]) -> None:
    """Handle events until the device disappears. Returns on disconnect; KeyboardInterrupt exits."""
    now = time.monotonic()
    next_config = now + CONFIG_POLL_S
    next_present = now + DEVICE_POLL_S
    while True:
        try:
            try:
                event = events.get(timeout=CONFIG_POLL_S)
                log.debug("%s", event)
                app.step(event)
            except queue.Empty:
                pass
            now = time.monotonic()
            if now >= next_present:
                next_present = now + DEVICE_POLL_S
                if not present():
                    log.warning("Launchpad disconnected")
                    return
            if now >= next_config:
                next_config = now + CONFIG_POLL_S
                _maybe_reload(watcher, app, audio)
        except Exception:
            if present():
                raise  # a real bug, not an unplug
            log.warning("Launchpad disconnected")
            return


def _maybe_reload(watcher: ConfigWatcher, app: App, audio) -> None:
    try:
        layout = watcher.check()
    except ConfigError as e:
        log.error("Config not reloaded, keeping the current layout: %s", e)
        return
    if layout is None:
        return
    log.info("Config reloaded from %s", watcher.path or "packaged default")
    app.layout = layout
    audio.set_targets(*_audio_targets(layout))
    audio.request_refresh()  # app volumes for any new apps arrive as an AudioChanged
    app.refresh()


def _audio_targets(layout: Layout):
    apps = {a.name: a.patterns for a in layout.apps}
    ignore = layout.active_app.ignore if layout.active_app else None
    return apps, ignore


def init_config() -> int:
    path = config.user_config_path()
    if path.exists():
        print(f"{path} already exists; not overwriting.", file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config.default_config_text())
    print(f"Wrote {path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="log every event")
    parser.add_argument("-c", "--config", type=Path,
                        help=f"config file (default: {config.user_config_path()}, "
                             "falling back to the built-in default)")
    parser.add_argument("--init-config", action="store_true",
                        help="copy the default config to the user config path and exit")
    parser.add_argument("--check-config", action="store_true",
                        help="validate the config and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.init_config:
        sys.exit(init_config())
    try:
        if args.check_config:
            path = config.resolve_path(args.config)
            config.load(path)
            print(f"OK: {path or 'packaged default'}")
            return
        run(args.config)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        sys.exit(2)  # the systemd unit does not restart on 2


if __name__ == "__main__":
    main()
