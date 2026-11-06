# CLAUDE.md

Working instructions for Claude Code in this repo.

## Project
Desktop control software for a Novation **Launchpad Pro [MK3]** on **Pop!_OS 24 (COSMIC/Wayland, PipeWire)**, in Python with `mido` + `python-rtmidi`. Read `docs/HANDOFF.md` (context, protocol, decisions) and `docs/IDEAS.md` (backlog) before starting work.

## Hard facts (don't re-derive)
- Use the **LPProMK3 MIDI** port (not DIN or DAW) for LEDs in Programmer mode.
- SysEx header `00 20 29 02 0E` (mido adds F0/F7). Programmer mode: `0E 01`; Live: `0E 00`.
- RGB LEDs: `03` then repeated `03 <idx> <r> <g> <b>`, colors **0–127**, ≤106 specs per message.
- Grid index = `row*10 + col`, bottom-left = 11.
- **Wayland**: no `xdotool`. Use uinput (`evdev`) or `ydotool` for synthetic input.
- Audio via PipeWire: `wpctl` / `pactl` / `pw-dump`.

## Conventions
- Python 3, venv at `.venv`. Keep dependencies minimal; add them to `pyproject.toml`.
- Device I/O stays in `device.py`; page logic must be testable against a fake device (no hardware in tests).
- LEDs render from **state**, not from press history.
- Always restore the device (clear LEDs, Live mode) on exit, including on exceptions.
- Batch LED updates; send only changed LEDs.

## Testing
- `pytest` for unit tests with the fake device.
- `scripts/lp_hello.py` is the hardware smoke test. Run it whenever device-layer code changes.

## Unverified (check before relying on them)
- Edge button CC numbers (record them in `docs/BUTTON_MAP.md`)
- Pressure message type in Programmer mode (polytouch vs channel aftertouch)
