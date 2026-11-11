"""Launchpad Pro [MK3] I/O. All MIDI traffic lives here.

Usage:
    with LaunchpadDevice() as dev:
        dev.set_event_handler(handler)
        dev.set_leds([(81, 127, 0, 0)])
    # On exit (including exceptions): LEDs cleared, device back in Live mode.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, Iterable, Protocol

import mido

from .events import ButtonChange, Event, PadPress, PadPressure, PadRelease
from .grid import LedSpec

log = logging.getLogger(__name__)

SYSEX_HEADER = [0x00, 0x20, 0x29, 0x02, 0x0E]  # Novation, LP Pro MK3 (mido adds F0/F7)
MAX_SPECS_PER_SYSEX = 106
LAUNCHPAD_USB_ID = ("1235", "0123")  # Focusrite - Novation, Launchpad Pro MK3
PROC_ASOUND = Path("/proc/asound")

EventHandler = Callable[[Event], None]


class Device(Protocol):
    """What the app needs from a device. Implemented by LaunchpadDevice and FakeDevice."""

    def set_event_handler(self, handler: EventHandler) -> None: ...
    def set_leds(self, specs: Iterable[LedSpec]) -> None: ...


def find_port(names: list[str]) -> str:
    """Pick the main 'LPProMK3 MIDI' interface, not DIN or DAW."""
    for n in names:
        if "LPProMK3 MIDI" in n:
            return n
    for n in names:
        if "Launchpad Pro MK3" in n and "MIDI 1" in n:
            return n
    raise LookupError(f"Launchpad Pro MK3 not found. Ports seen: {names}")


def launchpad_present(asound: Path = PROC_ASOUND) -> bool:
    """True while the Launchpad's ALSA sound card exists (used to spot unplug/replug).

    This is polled every 2s, so it reads /proc instead of listing MIDI ports. Listing
    ports (mido.get_input_names) walks every ALSA sequencer client, and each empty
    client slot makes the kernel wait on a driver-autoload worker: ~440 context switches
    per call, against none here. The card can appear a moment before its MIDI ports do;
    opening the device then fails and is retried.
    """
    want = ":".join(LAUNCHPAD_USB_ID)
    for usbid in asound.glob("card*/usbid"):  # only USB cards have one
        try:
            if usbid.read_text().strip() == want:
                return True
        except OSError:
            continue  # card removed while we looked
    return False


# --- USB bus visibility ------------------------------------------------------------
# After boot, and occasionally after suspend, the kernel enumerates the Launchpad but its
# firmware refuses SET_CONFIGURATION ("can't set config #1, error -32"). It then sits on
# the bus with no MIDI ports. Software can't fix that: USB resets and re-configuring were
# tried (0 of 6 worked, and resetting a healthy pad can itself trigger it). Only losing
# power, i.e. unplugging it, clears it. All we can do is notice and say so.

SYSFS_USB = Path("/sys/bus/usb/devices")


def launchpad_on_usb_bus(sysfs: Path = SYSFS_USB) -> bool:
    """True if the Launchpad is on the USB bus at all, configured or not (read-only, no root)."""
    vid, pid = LAUNCHPAD_USB_ID
    for entry in sorted(sysfs.iterdir()) if sysfs.is_dir() else []:
        try:
            if ((entry / "idVendor").read_text().strip() == vid
                    and (entry / "idProduct").read_text().strip() == pid):
                return True
        except OSError:
            continue  # interface entries (1-4:1.0) have no idVendor; devices come and go
    return False


def parse_message(msg: mido.Message) -> Event | None:
    """Translate a raw MIDI message into an app event. Returns None for anything ignored."""
    if msg.type == "note_on":
        if msg.velocity > 0:
            return PadPress(msg.note, msg.velocity)
        return PadRelease(msg.note)
    if msg.type == "note_off":
        return PadRelease(msg.note)
    if msg.type == "polytouch":
        return PadPressure(msg.note, msg.value)
    if msg.type == "control_change":
        return ButtonChange(msg.control, msg.value)
    return None


def _drop_clock(port: mido.ports.BaseInput) -> None:
    """Have rtmidi discard MIDI clock before it reaches Python.

    The pad sends clock all the time (48 messages/s at its default 120 BPM, unless
    "Transmit Clock" is off in its MIDI settings). mido only filters active sensing, so
    each tick would otherwise take the GIL and build a Message just to be ignored.
    mido has no public API for this, hence the private `_rt` (the rtmidi.MidiIn).
    """
    try:
        port._rt.ignore_types(sysex=False, timing=True, active_sense=True)  # type: ignore[attr-defined]
    except AttributeError:
        log.warning("Couldn't filter MIDI clock in rtmidi (mido internals changed?); "
                    "harmless, but uses more CPU")


def led_sysex_payloads(specs: list[LedSpec]) -> list[list[int]]:
    """Build SysEx data lists (header included, no F0/F7), max 106 RGB specs each."""
    payloads = []
    for start in range(0, len(specs), MAX_SPECS_PER_SYSEX):
        data = SYSEX_HEADER + [0x03]
        for i, r, g, b in specs[start:start + MAX_SPECS_PER_SYSEX]:
            data += [0x03, i, r, g, b]
        payloads.append(data)
    return payloads


class LaunchpadDevice:
    def __init__(self) -> None:
        self._in: mido.ports.BaseInput | None = None
        self._out: mido.ports.BaseOutput | None = None
        self._handler: EventHandler | None = None
        self._lit: set[int] = set()

    def __enter__(self) -> "LaunchpadDevice":
        in_name = find_port(mido.get_input_names())
        out_name = find_port(mido.get_output_names())
        log.info("Opening %s", in_name)
        self._out = mido.open_output(out_name)
        try:
            self._in = mido.open_input(in_name, callback=self._on_message)
            _drop_clock(self._in)
            self._sysex(0x0E, 0x01)  # Programmer mode
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def set_event_handler(self, handler: EventHandler) -> None:
        self._handler = handler

    def set_leds(self, specs: Iterable[LedSpec]) -> None:
        specs = list(specs)
        if not specs or self._out is None:
            return
        for data in led_sysex_payloads(specs):
            self._out.send(mido.Message("sysex", data=data))
        for i, r, g, b in specs:
            if (r, g, b) == (0, 0, 0):
                self._lit.discard(i)
            else:
                self._lit.add(i)

    def close(self) -> None:
        """Restore the device: clear every LED we lit, return to Live mode.

        If the device was unplugged, the restore fails; that is logged, not raised.
        """
        if self._in is not None:
            try:
                self._in.close()
            except Exception:
                log.debug("Closing the input port failed", exc_info=True)
            self._in = None
        if self._out is not None:
            try:
                self.set_leds([(i, 0, 0, 0) for i in sorted(self._lit)])
                time.sleep(0.05)
                self._sysex(0x0E, 0x00)  # Live mode
            except Exception as e:
                log.warning("Could not restore the Launchpad (unplugged?): %s", e)
            finally:
                try:
                    self._out.close()
                except Exception:
                    log.debug("Closing the output port failed", exc_info=True)
                self._out = None
                self._lit.clear()

    def _sysex(self, *data: int) -> None:
        assert self._out is not None
        self._out.send(mido.Message("sysex", data=SYSEX_HEADER + list(data)))

    def _on_message(self, msg: mido.Message) -> None:
        # Runs on the rtmidi thread; the handler must be thread-safe (e.g. queue.put).
        event = parse_message(msg)
        if event is not None and self._handler is not None:
            self._handler(event)
