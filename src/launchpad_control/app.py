"""Main loop: events in -> state -> frame -> diffed LED updates out."""
from __future__ import annotations

import argparse
import logging
import queue
import random
import signal
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Callable, ContextManager, Protocol

from . import config, grid, idle
from .config import ConfigError, Layout
from .device import Device
from .events import (AudioChanged, ButtonChange, DiscordChanged, Event, MediaChanged,
                     PadPress)
from .integrations.discord import DiscordState
from .integrations.media import MediaState
from .integrations.pipewire import AudioState

log = logging.getLogger(__name__)

CONFIG_POLL_S = 1.0
PAUSED_BRIGHTNESS = 0.25  # play/pause pad while the player is paused
DEVICE_POLL_S = 2.0  # how often to check the Launchpad is still (or again) plugged in
WAKE_GAP_S = 3.0  # more suspended time than this between two checks means the PC slept
WAKE_SETTLE_S = 3.0  # after waking, give the USB bus and the pad this long before reopening
STUCK_GRACE_S = 6.0  # on the USB bus but without MIDI ports for this long = stuck


class AudioControl(Protocol):
    def toggle_mic_mute(self) -> None: ...
    def set_master_volume(self, level: float) -> None: ...
    def set_app_volume(self, name: str, level: float) -> None: ...
    def set_active_volume(self, level: float) -> None: ...


class DiscordControl(Protocol):
    def toggle_mute(self) -> None: ...
    def toggle_deafen(self) -> None: ...


class MediaControl(Protocol):
    def previous(self) -> None: ...
    def play_pause(self) -> None: ...
    def next(self) -> None: ...


class App:
    """Holds all state; outlives device sessions so a replugged Launchpad repaints at once."""

    def __init__(self, device: Device | None, audio: AudioControl, layout: Layout,
                 discord: DiscordControl | None = None,
                 media: MediaControl | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 rng: random.Random | None = None) -> None:
        self.audio = audio
        self.discord = discord
        self.media = media
        self.layout = layout
        self.state = AudioState()
        self.discord_state = DiscordState()
        self.media_state = MediaState()
        self.clock = clock
        self.rng = rng or random.Random()
        self.idle: idle.IdleMode | None = None  # the running idle animation, if any
        self.attach(device)

    def attach(self, device: Device | None) -> None:
        """Use a (new) device. Its LEDs start blank, so the next refresh sends everything."""
        self.device = device
        self._differ = grid.FrameDiffer()
        self.idle = None
        self._last_activity = self.clock()

    def handle(self, event: Event) -> None:
        if isinstance(event, PadPress) or (isinstance(event, ButtonChange) and event.value):
            self._last_activity = self.clock()
            if self.idle is not None:
                log.debug("Waking from idle mode")
                self.idle = None
                return  # the waking press is swallowed, so it can't change a volume
        # Presses only act on the system. LEDs change when PipeWire reports the new state.
        if isinstance(event, PadPress):
            self._on_press(event.idx)
        elif isinstance(event, AudioChanged):
            if event.state.active_app != self.state.active_app and self.layout.active_app:
                log.info("Active-app fader now controls: %s", event.state.active_app or "nothing")
            self.state = event.state
        elif isinstance(event, DiscordChanged):
            if event.state.connected != self.discord_state.connected:
                log.info("Discord %s", "connected" if event.state.connected else "disconnected")
            self.discord_state = event.state
        elif isinstance(event, MediaChanged):
            self.media_state = event.state

    def _on_press(self, idx: int) -> None:
        layout = self.layout
        if idx == layout.mic_mute_pad:
            self.audio.toggle_mic_mute()
            return
        if layout.discord and idx in (layout.discord.mute_pad, layout.discord.deafen_pad):
            if self.discord is None:
                log.info("Discord isn't set up (restart after adding [discord] to the config)")
            elif idx == layout.discord.mute_pad:
                self.discord.toggle_mute()
            else:
                self.discord.toggle_deafen()
            return
        if layout.media and idx in layout.media.pads():
            if self.media is not None:
                m = layout.media
                action = (self.media.previous if idx == m.previous_pad
                          else self.media.play_pause if idx == m.play_pause_pad
                          else self.media.next)
                action()
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

    def tick(self) -> None:
        """Start, advance or stop the idle animation as time passes."""
        cfg = self.layout.idle
        if cfg is None:
            self.idle = None
            return
        now = self.clock()
        if self.idle is not None and self.idle.name != cfg.mode:
            self.idle = None  # mode changed in the config: start the new one below
        if self.idle is None:
            if now - self._last_activity >= cfg.after:
                log.debug("Idle for %.0fs: starting %s", now - self._last_activity, cfg.mode)
                self.idle = idle.MODES[cfg.mode](self.rng)
                self._next_frame = now + cfg.interval
        elif now >= self._next_frame:
            self.idle.step()
            # Keep a steady pace, but don't try to catch up after a stall.
            self._next_frame = max(self._next_frame + cfg.interval, now)

    def seconds_until_tick(self) -> float:
        """How long the main loop may block before tick() has something to do."""
        cfg = self.layout.idle
        if cfg is None:
            return float("inf")
        due = self._next_frame if self.idle else self._last_activity + cfg.after
        return max(0.0, due - self.clock())

    def render(self) -> grid.Frame:
        if self.idle is not None:
            return self.idle.render()
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
        if layout.media:
            m, st = layout.media, self.media_state
            for pad in m.pads():
                if not st.available:
                    frame[pad] = grid.DIM_GREY
                elif pad == m.play_pause_pad and not st.playing:
                    frame[pad] = grid.scale(m.color, PAUSED_BRIGHTNESS)
                else:
                    frame[pad] = m.color
        if layout.discord:
            d = self.discord_state
            # Deafened means nobody hears you either, so the mute pad goes red too.
            for pad, on in ((layout.discord.mute_pad, d.muted or d.deafened),
                            (layout.discord.deafen_pad, d.deafened)):
                if pad is not None:
                    frame[pad] = (grid.DIM_GREY if not d.connected
                                  else grid.RED if on else grid.DIM_BLURPLE)
        return frame

    def refresh(self) -> None:
        if self.device is not None:
            self.device.set_leds(self._differ.diff(self.render()))

    def step(self, event: Event | None = None) -> None:
        if event is not None:
            self.handle(event)
        self.tick()
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
    from .device import LaunchpadDevice, launchpad_on_usb_bus, launchpad_present
    from .integrations.discord import Discord
    from .integrations.media import Media
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

    media = Media()  # stateless to set up, so a config reload can add [media] live
    discord = None
    if layout.discord:
        cfg_dir = config.user_config_dir()
        discord = Discord(layout.discord.client_id, cfg_dir / "discord-secret",
                          cfg_dir / "discord-token.json")

    audio.watch(lambda s: events.put(AudioChanged(s)))  # also sends the initial state
    if discord:
        discord.watch(lambda s: events.put(DiscordChanged(s)))
    media.watch(lambda s: events.put(MediaChanged(s)))
    log.info("Running. Ctrl+C to quit.")
    try:
        serve(watcher, layout, audio, events, LaunchpadDevice, launchpad_present, discord,
              media, StuckDeviceNotice(launchpad_on_usb_bus))
    except KeyboardInterrupt:
        pass
    finally:
        audio.stop()
        media.stop()
        if discord:
            discord.stop()


class SleepWatch:
    """Notices that the PC was suspended, using the gap between two kernel clocks.

    CLOCK_BOOTTIME keeps counting during suspend and CLOCK_MONOTONIC does not, so their
    difference grows only while asleep (unlike wall time, NTP can't fake it).
    """

    def __init__(self, boottime: Callable[[], float] | None = None,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._boot = boottime or (lambda: time.clock_gettime(time.CLOCK_BOOTTIME))
        self._mono = monotonic
        self._asleep = self._boot() - self._mono()

    def woke(self) -> bool:
        """True once per suspend/resume since the last call."""
        asleep = self._boot() - self._mono()
        slept, self._asleep = asleep - self._asleep, asleep
        return slept > WAKE_GAP_S


class StuckDeviceNotice:
    """Says so, once per episode, when the Launchpad is on the USB bus but has no MIDI ports.

    That is the kernel failing to configure it after boot/resume (`error -32`). Resets
    don't clear it, only unplugging does, so the best we can do is tell the user why.
    """

    def __init__(self, on_bus: Callable[[], bool],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._on_bus, self._clock = on_bus, clock
        self._stuck_since: float | None = None
        self._told = False

    def poll(self, connected: bool) -> None:
        if connected or not self._on_bus():
            self._stuck_since, self._told = None, False
            return
        now = self._clock()
        if self._stuck_since is None:
            self._stuck_since = now
        if not self._told and now - self._stuck_since >= STUCK_GRACE_S:
            self._told = True
            log.warning("The Launchpad is on the USB bus but the kernel never configured it "
                        "(see `journalctl -k | grep 'set config'`). Unplug and replug it.")


def serve(watcher: ConfigWatcher, layout: Layout, audio, events: "queue.Queue[Event]",
          open_device: Callable[[], ContextManager[Device]],
          present: Callable[[], bool], discord: DiscordControl | None = None,
          media: MediaControl | None = None, stuck: StuckDeviceNotice | None = None,
          sleep_watch: SleepWatch | None = None) -> None:
    """Run until interrupted, surviving the Launchpad being absent, unplugged or replugged.

    The App (state and layout) carries over between device sessions, so a replugged
    Launchpad is repainted from the current state straight away. After the PC wakes from
    suspend the session is dropped and reopened: the kernel has reset the pad by then, but
    since it never left the USB bus, no unplug is seen and it would otherwise stay in the
    wrong mode with stale LEDs.
    """
    app = App(None, audio, layout, discord, media)
    sleep_watch = sleep_watch or SleepWatch()
    waiting_logged = False
    while True:
        stack = ExitStack()
        try:
            connected = present()
            if stuck is not None:
                stuck.poll(connected)
            if not connected:
                raise LookupError("not connected")
            dev = stack.enter_context(open_device())
        except Exception as e:
            if not waiting_logged:
                log.info("Waiting for the Launchpad (%s)", e)
                waiting_logged = True
            _wait_for_device(events, app)
            continue
        waiting_logged = False
        with stack:
            log.info("Launchpad connected")
            app.attach(dev)
            dev.set_event_handler(events.put)
            sleep_watch.woke()  # a suspend before this point is already behind us
            try:
                app.refresh()
                woke = _session(app, watcher, audio, events, present, sleep_watch)
            finally:
                app.attach(None)
        if woke:
            _settle(events, app, WAKE_SETTLE_S)


def _wait_for_device(events: "queue.Queue[Event]", app: App) -> None:
    """Wait for the device to show up, still tracking state so the queue can't grow.

    Leftover pad presses from before an unplug are dropped, not acted on.
    """
    try:
        event = events.get(timeout=DEVICE_POLL_S)
    except queue.Empty:
        return
    _track_state(app, event)


def _track_state(app: App, event: Event | None) -> None:
    """Apply state updates only; presses and buttons are meaningless without a live device."""
    if isinstance(event, (AudioChanged, DiscordChanged, MediaChanged)):
        app.handle(event)


def _settle(events: "queue.Queue[Event]", app: App, seconds: float) -> None:
    """Keep tracking state for `seconds` without a device (lets a woken USB bus settle)."""
    deadline = time.monotonic() + seconds
    while (left := deadline - time.monotonic()) > 0:
        try:
            event = events.get(timeout=left)
        except queue.Empty:
            return
        _track_state(app, event)


def _session(app: App, watcher: ConfigWatcher, audio, events: "queue.Queue[Event]",
             present: Callable[[], bool], sleep_watch: SleepWatch) -> bool:
    """Handle events until the device disappears or the PC wakes from suspend.

    Returns True after a wake (the caller reopens the device) and False on a disconnect;
    KeyboardInterrupt exits.
    """
    now = time.monotonic()
    next_config = now + CONFIG_POLL_S
    next_present = now + DEVICE_POLL_S
    while True:
        try:
            event = None
            try:
                event = events.get(timeout=min(CONFIG_POLL_S, app.seconds_until_tick()))
                log.debug("%s", event)
            except queue.Empty:
                pass
            if sleep_watch.woke():
                log.info("PC woke from suspend; reopening the Launchpad")
                _track_state(app, event)  # a press from before the nap must not act
                return True
            app.step(event)
            now = time.monotonic()
            if now >= next_present:
                next_present = now + DEVICE_POLL_S
                if not present():
                    log.warning("Launchpad disconnected")
                    return False
            if now >= next_config:
                next_config = now + CONFIG_POLL_S
                _maybe_reload(watcher, app, audio)
        except Exception:
            if present():
                raise  # a real bug, not an unplug
            log.warning("Launchpad disconnected")
            return False


def _maybe_reload(watcher: ConfigWatcher, app: App, audio) -> None:
    try:
        layout = watcher.check()
    except ConfigError as e:
        log.error("Config not reloaded, keeping the current layout: %s", e)
        return
    if layout is None:
        return
    log.info("Config reloaded from %s", watcher.path or "packaged default")
    old_id = app.layout.discord.client_id if app.layout.discord else None
    new_id = layout.discord.client_id if layout.discord else None
    if new_id != old_id:
        log.warning("Discord client_id changed: restart to apply (lpctl restart)")
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
    parser =argparse.ArgumentParser(description=__doc__)
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
