# launchpad-control

Custom desktop control software for a **Novation Launchpad Pro [MK3]** on **Pop!_OS 24 (COSMIC, Wayland, PipeWire)**.

Turns the 8×8 RGB pad grid into a control surface for the desktop: per-app volume, mic mute, a soundboard that plays into the mic, Discord and media controls, home server status, notifications, and more. The point is two-way control: pads trigger actions, and LEDs show the real state of the system.

## Status

- ✅ `scripts/lp_hello.py` works on hardware: enters Programmer mode, sets RGB per pad, lights pads on press (velocity → brightness), logs all incoming MIDI, restores Live mode on exit.
- ✅ **Mic mute** (pad 18, bottom-right): toggles system mic mute; red = muted, dim green = live.
- ✅ **Volume faders**: column 1 = master (green), column 2 = Firefox (orange), column 3 = Discord (purple). Press a pad to set the level (row × 12.5%); press the bottom pad again to go to 0. An app that isn't playing shows only a dim bottom pad.
- ✅ **Active-app fader** (column 4, cyan): controls whatever else is playing (e.g. a game): the newest audio stream not claimed by another fader, skipping notification sounds, paused streams and an ignore list. The log says which app it's controlling.
- All LEDs follow the real PipeWire state, including changes made elsewhere (keyboard, panel, pavucontrol).
- ✅ **Config file**: layout, colours and app matching live in TOML (see below), reloaded live.
- ⏭️ Next: map the edge buttons (CCs), page system.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/launchpad-control        # -v logs every event; Ctrl+C restores the device
.venv/bin/pytest                   # unit tests, no hardware needed
.venv/bin/python scripts/lp_hello.py   # hardware smoke test
```

## Configuration

The layout lives in `~/.config/launchpad-control/config.toml`. Without that file, the built-in
[default](src/launchpad_control/default_config.toml) is used.

```bash
.venv/bin/launchpad-control --init-config    # copy the default there to edit
.venv/bin/launchpad-control --check-config   # validate without starting
.venv/bin/launchpad-control -c other.toml    # use a different file
```

Edits are picked up within a second while the app runs. If an edit is invalid, the error is
logged and the current layout stays active.

## Docs

- [`docs/HANDOFF.md`](docs/HANDOFF.md): project context, decisions, hardware/protocol reference, Linux integration notes, open questions
- [`docs/IDEAS.md`](docs/IDEAS.md): feature backlog, roughly prioritized
- [`CLAUDE.md`](CLAUDE.md): working instructions for Claude Code sessions in this repo
