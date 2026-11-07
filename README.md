# launchpad-control

Custom desktop control software for a **Novation Launchpad Pro [MK3]** on **Pop!_OS 24 (COSMIC, Wayland, PipeWire)**.

Turns the 8×8 RGB pad grid into a control surface for the desktop: per-app volume, mic mute, a soundboard that plays into the mic, Discord and media controls, home server status, notifications, and more. The point is two-way control: pads trigger actions, and LEDs show the real state of the system.

## Status

- ✅ `scripts/lp_hello.py` works on hardware: enters Programmer mode, sets RGB per pad, lights pads on press (velocity → brightness), logs all incoming MIDI, restores Live mode on exit.
- ✅ **Mic mute** (pad 18, bottom-right): toggles system mic mute; red = muted, dim green = live.
- ✅ **Volume faders**: column 1 = master (green), column 2 = Firefox (orange), column 3 = Discord (purple). Press a pad to set the level (row × 12.5%); press the bottom pad again to go to 0. An app that isn't playing shows only a dim bottom pad.
- All LEDs follow the real PipeWire state, including changes made elsewhere (keyboard, panel, pavucontrol).
- ⏭️ Next: config file for the layout, map the edge buttons (CCs), page system.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/launchpad-control        # -v logs every event; Ctrl+C restores the device
.venv/bin/pytest                   # unit tests, no hardware needed
.venv/bin/python scripts/lp_hello.py   # hardware smoke test
```

## Docs

- [`docs/HANDOFF.md`](docs/HANDOFF.md): project context, decisions, hardware/protocol reference, Linux integration notes, open questions
- [`docs/IDEAS.md`](docs/IDEAS.md): feature backlog, roughly prioritized
- [`CLAUDE.md`](CLAUDE.md): working instructions for Claude Code sessions in this repo
