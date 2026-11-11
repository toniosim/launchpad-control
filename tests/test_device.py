import mido
import pytest

from launchpad_control.device import find_port, led_sysex_payloads, parse_message, SYSEX_HEADER
from launchpad_control.events import ButtonChange, PadPress, PadPressure, PadRelease

LINUX_PORTS = [
    "Midi Through:Midi Through Port-0 14:0",
    "Launchpad Pro MK3:Launchpad Pro MK3 LPProMK3 MIDI 32:0",
    "Launchpad Pro MK3:Launchpad Pro MK3 LPProMK3 DIN 32:1",
    "Launchpad Pro MK3:Launchpad Pro MK3 LPProMK3 DAW 32:2",
]


def test_find_port_picks_midi_interface():
    assert find_port(LINUX_PORTS) == LINUX_PORTS[1]


def test_find_port_missing():
    with pytest.raises(LookupError):
        find_port(LINUX_PORTS[:1])


def test_parse_messages():
    assert parse_message(mido.Message("note_on", note=81, velocity=90)) == PadPress(81, 90)
    assert parse_message(mido.Message("note_on", note=81, velocity=0)) == PadRelease(81)
    assert parse_message(mido.Message("note_off", note=81)) == PadRelease(81)
    assert parse_message(mido.Message("polytouch", note=81, value=40)) == PadPressure(81, 40)
    assert parse_message(mido.Message("control_change", control=91, value=127)) == ButtonChange(91, 127)
    assert parse_message(mido.Message("clock")) is None


def test_led_payload_format():
    [data] = led_sysex_payloads([(11, 1, 2, 3), (12, 4, 5, 6)])
    assert data == SYSEX_HEADER + [0x03, 0x03, 11, 1, 2, 3, 0x03, 12, 4, 5, 6]


def test_led_payloads_split_at_106():
    specs = [(i % 100, 1, 1, 1) for i in range(250)]
    payloads = led_sysex_payloads(specs)
    assert [(len(p) - len(SYSEX_HEADER) - 1) // 5 for p in payloads] == [106, 106, 38]


# --- USB bus visibility ----------------------------------------------------------------

from launchpad_control import device  # noqa: E402


def make_sysfs(tmp_path, vid="1235", pid="0123"):
    sysfs = tmp_path / "sys"
    (sysfs / "1-4:1.0").mkdir(parents=True)  # interface entry: no idVendor
    other = sysfs / "1-3"
    other.mkdir()
    (other / "idVendor").write_text("1532\n")
    (other / "idProduct").write_text("00c4\n")
    pad = sysfs / "1-4"
    pad.mkdir()
    (pad / "idVendor").write_text(vid + "\n")
    (pad / "idProduct").write_text(pid + "\n")
    return sysfs


def test_launchpad_on_usb_bus(tmp_path):
    assert device.launchpad_on_usb_bus(make_sysfs(tmp_path))


def test_launchpad_not_on_usb_bus(tmp_path):
    assert not device.launchpad_on_usb_bus(make_sysfs(tmp_path, vid="dead"))
    assert not device.launchpad_on_usb_bus(tmp_path / "missing")
