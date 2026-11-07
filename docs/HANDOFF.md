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
| Media | `playerctl` (MPRIS) | play/pause/next, now-playing metadata for colors |
| Discord | Mute/deafen via system mic mute (above) or Discord keybinds sent through uinput | Discord's global keybinds are unreliable under Wayland, and its RPC voice-control scopes are restricted. Decide approach later (open question) |
| OBS | obs-websocket (built into OBS 28+) | scene switch, record/stream state for LEDs |
| Home server / Home Assistant | HTTP APIs (HA REST/WebSocket, container health endpoints) | status grid + actions |
| Running in background | systemd **user** service (done: `systemd/launchpad-control.service` template, installed by `scripts/install-service.sh`) | reconnect loop is `app.serve`; polls `launchpad_present()` every 2s since rtmidi gives no unplug callback. Exit code 2 (config error) is not restarted |

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

1. Discord control path: plain system mic mute vs. uinput keybinds vs. RPC. Which Discord actions are actually needed beyond mute/deafen?
2. Soundboard audio backend: play via `pw-play`/`paplay` subprocesses targeted at the virtual sink, or an in-process mixer (e.g. `sounddevice` + numpy) for choke groups, looping, and velocity volume?
3. Async model: plain threads vs. `asyncio` (helpful once there are websocket integrations like OBS and HA).
4. How to test without hardware: a fake device that records LED frames would let the page logic be unit-tested (and maybe render to a terminal or web preview).
