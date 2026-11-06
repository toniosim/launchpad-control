# launchpad-control

Custom desktop control software for a **Novation Launchpad Pro [MK3]** on **Pop!_OS 24 (COSMIC, Wayland, PipeWire)**.

Turns the 8×8 RGB pad grid into a control surface for the desktop: per-app volume, mic mute, a soundboard that plays into the mic, Discord and media controls, home server status, notifications, and more. The point is two-way control: pads trigger actions, and LEDs show the real state of the system.

## Status

- ✅ `scripts/lp_hello.py` works on hardware: enters Programmer mode, sets RGB per pad, lights pads on press (velocity → brightness), logs all incoming MIDI, restores Live mode on exit.
- ⏭️ Next: map the edge buttons (CCs), then build the first real feature (mic mute with LED synced to state).

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install mido python-rtmidi
python scripts/lp_hello.py
```

## Docs

- [`docs/HANDOFF.md`](docs/HANDOFF.md): project context, decisions, hardware/protocol reference, Linux integration notes, open questions
- [`docs/IDEAS.md`](docs/IDEAS.md): feature backlog, roughly prioritized
- [`CLAUDE.md`](CLAUDE.md): working instructions for Claude Code sessions in this repo
