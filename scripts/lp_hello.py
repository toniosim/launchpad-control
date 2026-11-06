#!/usr/bin/env python3
"""
Launchpad Pro [MK3] starter: enter Programmer mode, paint the grid,
light pads on press, and print every incoming message so you can
map the side/top/bottom buttons yourself.

Setup (Pop!_OS):
    python3 -m venv ~/.venvs/launchpad
    source ~/.venvs/launchpad/bin/activate
    pip install mido python-rtmidi

Run:
    python lp_hello.py          # Ctrl+C to quit (returns device to Live mode)
"""
import time
import mido

SYSEX_HEADER = [0x00, 0x20, 0x29, 0x02, 0x0E]  # Novation, Launchpad Pro MK3 (mido adds F0/F7)


def find_port(names):
    """Pick the main 'LPProMK3 MIDI' interface, not DIN or DAW."""
    for n in names:
        if "LPProMK3 MIDI" in n or ("Launchpad Pro MK3" in n and "MIDI 1" in n):
            return n
    # Fallback: first Launchpad port that isn't DIN/DAW
    for n in names:
        if "Launchpad" in n and "DIN" not in n and "DAW" not in n:
            return n
    raise SystemExit(f"Launchpad not found. Ports seen: {names}")


def sysex(out, *data):
    out.send(mido.Message("sysex", data=SYSEX_HEADER + list(data)))


def programmer_mode(out, on=True):
    sysex(out, 0x0E, 0x01 if on else 0x00)


def set_rgb(out, leds):
    """leds: list of (index, r, g, b), each colour 0-127. One SysEx for many LEDs."""
    data = [0x03]
    for idx, r, g, b in leds:
        data += [0x03, idx, r, g, b]  # type 03h = RGB
    sysex(out, *data)


def grid_indices():
    """8x8 pads in Programmer numbering: row*10 + col, bottom-left = 11."""
    return [(r, c, r * 10 + c) for r in range(1, 9) for c in range(1, 9)]


def main():
    in_name = find_port(mido.get_input_names())
    out_name = find_port(mido.get_output_names())
    print(f"in:  {in_name}\nout: {out_name}")

    with mido.open_input(in_name) as inp, mido.open_output(out_name) as out:
        programmer_mode(out, True)

        # Gradient across the grid so you can confirm the numbering
        base = {}
        for r, c, idx in grid_indices():
            base[idx] = (c * 15, r * 15, 127 - c * 12)
        set_rgb(out, [(i, *rgb) for i, rgb in base.items()])

        print("Press pads/buttons. Ctrl+C to exit.")
        try:
            for msg in inp:
                print(msg)
                if msg.type == "note_on" and msg.note in base:
                    if msg.velocity > 0:
                        v = msg.velocity  # harder hit = brighter white
                        set_rgb(out, [(msg.note, v, v, v)])
                    else:
                        set_rgb(out, [(msg.note, *base[msg.note])])
                elif msg.type == "note_off" and msg.note in base:
                    set_rgb(out, [(msg.note, *base[msg.note])])
                # Buttons arrive as control_change; pressure as polytouch/aftertouch.
        except KeyboardInterrupt:
            pass
        finally:
            set_rgb(out, [(i, 0, 0, 0) for i in base])
            time.sleep(0.05)
            programmer_mode(out, False)


if __name__ == "__main__":
    main()
