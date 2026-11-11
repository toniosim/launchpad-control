# Handoff: launchpad-control

Context from the initial planning conversation (2026-11-06), so a new session can pick up without re-deriving anything.

## Goal

Build custom software that turns a Novation Launchpad Pro [MK3] into a desktop control surface. The original goals were:

1. Control system things directly (e.g. **volume**)
2. **Discord controls** (mute, deafen, etc.)
3. A **soundboard that plays into the mic channel**, so others hear it in Discord or games

These grew into a broader backlog (see `IDEAS.md`). The core design principle: **the LEDs reflect real state** (e.g. the mute pad is red because the mic is actually muted, even if it was muted elsewhere), not just "button was pressed."

## Environment

- **OS:** Pop!_OS 24 with the COSMIC desktop, which runs on **Wayland**
- **Audio:** PipeWire (control via `wpctl` / `pactl`; `pw-cli`/`pw-loopback` for routing)
- **Language:** Python 3 with `mido` + `python-rtmidi`, in a venv (system pip is externally managed on Pop 24)
- **Hardware:** Launchpad Pro **MK3** (not the original Launchpad Pro, which uses different SysEx)

## Verified so far

`scripts/lp_hello.py` was tested on the real device and works:
- Finds the correct port, enters Programmer mode, paints an RGB gradient
- Pad press → white at brightness = velocity; release → restores base color
- Prints every incoming message (use this to map the buttons)
- Clears LEDs and returns to Live mode on Ctrl+C

## Hardware / protocol reference (Launchpad Pro MK3)

Source: Novation, *Launchpad Pro [MK3] Programmer's Reference Manual* (LPP3_prog_ref_guide_200415.pdf).

### MIDI ports
The device exposes three interfaces:
| Interface | Use |
|---|---|
| **LPProMK3 MIDI** | Note/Chord & Custom modes, **LED control in Custom and Programmer modes** ← use this one |
| LPProMK3 DIN | 5-pin DIN MIDI passthrough |
| LPProMK3 DAW | DAW/Session mode |

On Linux/ALSA the names may look like `Launchpad Pro MK3 ... MIDI 1/2/3`. `find_port()` in the starter script matches `LPProMK3 MIDI` or `Launchpad Pro MK3` + `MIDI 1`, and skips DIN and DAW.

### SysEx
All messages start with header `F0 00 20 29 02 0E` (mido adds the F0/F7 itself; pass `[0x00,0x20,0x29,0x02,0x0E, ...]`).

| Purpose | Message |
|---|---|
| Programmer mode on/off | `F0 00 20 29 02 0E 0E <mode> F7` (0 = Live, 1 = Programmer) |
| Select layout | `F0 00 20 29 02 0E 00 <layout> <page> 00 F7` (Programmer layout = `11h`) |
| Set LEDs | `F0 00 20 29 02 0E 03 <spec> [<spec>...] F7`, up to **106 specs per message** |
| Device inquiry (universal) | `F0 7E 7F 06 01 F7` |

LED spec = `<type> <led_index> <data...>`:
| Type | Meaning | Data |
|---|---|---|
| `00h` | static palette color | 1 byte palette index |
| `01h` | flashing | color B, color A |
| `02h` | pulsing | palette index |
| `03h` | **RGB** | R, G, B (each **0–127**) |

LED indices always use Programmer-mode numbering. While in Programmer mode, the device's Settings menu is unavailable; selecting any other layout exits it.

### LEDs via plain Note On (cheaper than SysEx for single pads)
- Channel 1 (`90h`): static, velocity = palette index
- Channel 2 (`91h`): flashing between current color and the given color, synced to MIDI clock (default 120 BPM; 1 period = 1 beat)
- Channel 3 (`92h`): pulsing dark↔full (1 period = 2 beats)
- Note Off / velocity 0: LED off

### Programmer-mode grid numbering
`index = row * 10 + col`, rows and columns 1–8, **bottom-left = 11**, bottom-right = 18, top-left = 81, top-right = 88.

### MIDI clock and resource use (measured 2026-11-11, don't re-derive)
- The pad **sends MIDI clock (`F8`) on the MIDI port all the time**: 48 messages/s at its default 120 BPM, even in Programmer mode. mido's rtmidi backend only filters active sensing, so `device._drop_clock` sets rtmidi's `ignore_types(timing=True)` (a private `_rt` access, since mido has no API for it). That cut the input thread from ~0.6% to ~0.13% of a core. The remaining 48 wakeups/s go away only if the pad stops sending: Setup page → fourth Track Select button (MIDI settings) → **Transmit Clock (Tx)** off.
- **Never list MIDI ports in a poll loop.** `mido.get_input_names()` walks every ALSA sequencer client, and each empty client slot in the per-card range makes the kernel wait on a driver-autoload worker: ~210 context switches in our thread (~440 system-wide) per call. Polled every 2s, that was ~105 wakeups/s. `launchpad_present()` reads `/proc/asound/card*/usbid` instead (0 switches).
- Baseline before these fixes: ~1% of a core, ~150 wakeups/s, 29.9 MB RSS. After them, with the pad still sending clock: ~0.28% and ~50 wakeups/s (normal use), 23.7 MB RSS. With Transmit Clock also off on the pad (done on the user's pad): 0.14% and 1.3 wakeups/s in normal use, 0.31% and ~5 wakeups/s while the idle animation runs. The idle animation adds ~0.15%, mostly the cost of waking 2.5 times/s, not the Life math, so it isn't worth optimizing. `urllib.request` is imported lazily in `discord.py` (it pulls in ssl, ~5 MB).

### Not yet verified (TODO)
- **Edge button CC numbers** (top row, left/right columns, the two bottom rows on the MK3). The guide shows them only in a diagram. → Run `lp_hello.py`, press each one, and record the map in `docs/BUTTON_MAP.md`.
- **Pressure/aftertouch**: the guide says aftertouch can be polyphonic or channel pressure depending on configuration. Check which one arrives in Programmer mode (`polytouch` vs `aftertouch` in mido) and whether it needs enabling in the device settings.
- Whether the LED indices for the edge buttons equal their CC numbers (likely, but confirm).

## Linux integration notes

| Need | Approach | Notes |
|---|---|---|
| Per-app / master volume | `wpctl set-volume <id> 5%+`, `pactl set-sink-input-volume` | List streams with `pactl list sink-inputs` or `pw-dump` (JSON) |
| Mic mute | `wpctl set-mute @DEFAULT_SOURCE@ toggle` | Works for every app, not just Discord. Poll `wpctl get-volume @DEFAULT_SOURCE@` (prints `[MUTED]`) or subscribe via `pactl subscribe` to keep the LED in sync |
| Soundboard into mic | Create a virtual sink for soundboard output plus a virtual source that mixes real mic + soundboard; select the virtual source as the mic in Discord/games | PipeWire options: `pactl load-module module-null-sink` + `module-remap-source` / loopback, or a persistent config in `~/.config/pipewire/pipewire.conf.d/`. Also route the soundboard to headphones so you can hear it |
| Keystrokes / hotkeys | `ydotool` (uinput) or a Python `evdev` uinput device | **`xdotool` does not work on Wayland/COSMIC.** uinput needs the user in the `input` group or a udev rule |
| Media | `playerctl` (MPRIS) | Done: `integrations/media.py`, actions + `--follow status` for LED state. Verified: it prints an **empty line** when the player goes away (and keeps running), so empty = no player. Now-playing metadata for colors is still open |
| Discord | **Local RPC** over the `discord-ipc-0` Unix socket (`integrations/discord.py`) | Decided: RPC, not keybinds (unreliable on Wayland). The restricted `rpc`/`rpc.voice.*` scopes work for the app's owner, so the user registers their own Discord app: Application ID in `[discord] client_id`, secret in `~/.config/launchpad-control/discord-secret`, token cached in `discord-token.json`. Flatpak socket is at `$XDG_RUNTIME_DIR/app/com.discordapp.Discord/`. Verified: handshake with the real client returns READY. More is possible over the same socket: per-user volume, speaking indicators, channel join, `NOTIFICATION_CREATE` (needs `rpc.notifications.read`, which means one more Authorize click) |
| OBS | obs-websocket (built into OBS 28+) | scene switch, record/stream state for LEDs |
| Home server / Home Assistant | HTTP APIs (HA REST/WebSocket, container health endpoints) | status grid + actions |
| Running in background | systemd **user** service (done: `systemd/launchpad-control.service` template, installed by `scripts/install-service.sh`) | reconnect loop is `app.serve`; polls `launchpad_present()` every 2s since rtmidi gives no unplug callback (it reads `/proc/asound`, not the MIDI port list; see "MIDI clock and resource use"). Exit code 2 (config error) is not restarted |
| Suspend/resume | **Two failure modes, from `journalctl -k` across 5 boots** (don't re-derive). (1) After resume xhci reports `root hub lost power or was reset` and resets the pad *in place* (`usb 1-4: reset full-speed USB device`): it never leaves the bus, so the 2s `present()` poll sees nothing and the app kept a stale session. Fix (done): `app.SleepWatch` (BOOTTIME minus MONOTONIC grows only while suspended) makes `_session` return, then `serve` waits `WAKE_SETTLE_S` and reopens. Verified on hardware (2026-11-11): suspend then wake, pad came back working. (2) `usb 1-4: can't set config #1, error -32` on **every cold boot** and ~3 of ~45 resumes: enumerated but unconfigured, no ALSA ports; the *firmware* stalls SET_CONFIGURATION. **Not fixable in software, tried and backed out**: `USBDEVFS_RESET` + `USBDEVFS_SETCONFIGURATION(1)` via a uaccess udev rule failed 6 of 6 from the stuck state (EPIPE), and resetting a *healthy* pad wedged it 1 of 2 times (same failure as the kernel's own resume reset, ~1 in 14). Only losing VBUS clears it; the pad is on a root-hub port with no known per-port power control. `app.StuckDeviceNotice` logs it after 6s. Untried: kernel quirks `usbcore.quirks=1235:0123:gp` (delay init / delay control msg; testable at runtime via `/sys/module/usbcore/parameters/quirks` plus a replug, but cold boot needs a reboot), `mem_sleep_default=s2idle` (xhci wouldn't be reset on resume at all, costs sleep power), or a powered hub with per-port switching (`uhubctl`) |

## Proposed architecture

```
launchpad-control/
├── src/launchpad_control/
│   ├── device.py        # port discovery, programmer mode, LED API (batched RGB sysex), reconnect
│   ├── grid.py          # index helpers, color utils, frame buffer → diff → minimal sysex
│   ├── app.py           # event loop, page switching, dispatch
│   ├── pages/           # one plugin per mode
│   │   ├── mixer.py
│   │   ├── soundboard.py
│   │   ├── server.py
│   │   └── macros.py
│   ├── integrations/    # pipewire.py, uinput.py, playerctl.py, obs.py, homeassistant.py
│   ├── config.py        # load + validate TOML layout
│   └── default_config.toml  # packaged default (user copy: ~/.config/launchpad-control/config.toml)
├── sounds/              # soundboard clips (gitignored if large)
├── scripts/lp_hello.py  # hardware smoke test
├── systemd/launchpad-control.service
└── tests/
```

Key design decisions so far:
- **Pages/modes** switched by edge buttons; each page is a plugin with `on_enter`, `on_pad(note, velocity)`, `on_button(cc, value)`, `on_pressure(note, value)`, `render() -> frame`.
- **Frame buffer + diff**: pages render a full 8×8 (+ buttons) frame; the device layer sends only changed LEDs, batched into one SysEx (≤106 specs). Keeps meters and visualizers smooth.
- **State-driven LEDs**: integrations publish state (mute, volume, service health); pages render from state, not from button history.
- **Config-driven mappings** in **TOML** (stdlib `tomllib`, no extra dependency; chosen over YAML). User file at `~/.config/launchpad-control/config.toml`, falling back to the packaged default; validated at startup with clear errors; reloaded live while running (an invalid edit is logged and the old layout kept).
- Always restore the device (clear and return to Live mode) on exit.

## Open questions

1. ~~Discord control path~~: decided, local RPC (see the integration notes). Still open: which Discord actions beyond mute/deafen are worth adding.
2. Soundboard audio backend: play via `pw-play`/`paplay` subprocesses targeted at the virtual sink, or an in-process mixer (e.g. `sounddevice` + numpy) for choke groups, looping, and velocity volume?
3. Async model: plain threads vs. `asyncio` (helpful once there are websocket integrations like OBS and HA).
4. How to test without hardware: a fake device that records LED frames would let the page logic be unit-tested (and maybe render to a terminal or web preview).
