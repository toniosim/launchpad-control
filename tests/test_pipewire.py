import json
import subprocess

from launchpad_control.integrations.pipewire import PipeWire, is_relevant_event, parse_sink_inputs


def stream(index, pct, **props):
    v = int(65536 * pct / 100)
    return {"index": index, "volume": {"front-left": {"value": v}, "front-right": {"value": v}},
            "properties": props}


SINK_INPUTS = json.dumps([
    stream(252, 100, **{"application.name": "speech-dispatcher-dummy"}),
    stream(4477, 40, **{"application.name": "Firefox", "application.process.binary": "firefox-bin"}),
    stream(4480, 70, **{"application.name": "Firefox", "application.process.binary": "firefox-bin"}),
    stream(5000, 25, **{"application.name": "WEBRTC VoiceEngine",
                        "application.process.binary": "Discord"}),
])

APPS = {"firefox": ["firefox"], "discord": ["discord"], "spotify": ["spotify"]}


class FakeRunner:
    """Answers pactl/wpctl commands from canned output and records every call."""

    def __init__(self, **outputs):
        self.outputs = outputs
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        for key, out in self.outputs.items():
            if key.replace("_", "-") in cmd:
                if isinstance(out, Exception):
                    raise out
                return out
        return ""


def test_mic_mute_parsing():
    assert PipeWire(run=FakeRunner(**{"get_source_mute": "Mute: yes\n"})).is_mic_muted() is True
    assert PipeWire(run=FakeRunner(**{"get_source_mute": "Mute: no\n"})).is_mic_muted() is False
    assert PipeWire(run=FakeRunner(**{"get_source_mute": "huh"})).is_mic_muted() is None


def test_failures_become_none():
    err = subprocess.CalledProcessError(1, "x")
    pw = PipeWire(APPS, run=FakeRunner(get_source_mute=err, get_volume=err, list=err))
    s = pw.snapshot()
    assert s.mic_muted is None and s.master_volume is None
    assert s.app_volumes == {"firefox": None, "discord": None, "spotify": None}


def test_master_volume_parsing():
    assert PipeWire(run=FakeRunner(get_volume="Volume: 0.75\n")).master_volume() == 0.75
    assert PipeWire(run=FakeRunner(get_volume="Volume: 0.40 [MUTED]\n")).master_volume() == 0.4


def test_set_master_volume_clamps():
    run = FakeRunner()
    PipeWire(run=run).set_master_volume(1.7)
    assert run.calls == [["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "1.000"]]


def test_parse_sink_inputs():
    streams = parse_sink_inputs(SINK_INPUTS)
    assert [s.index for s in streams] == [252, 4477, 4480, 5000]
    assert abs(streams[1].level - 0.4) < 0.001


def test_app_volumes_match_by_name_or_binary():
    pw = PipeWire(APPS, run=FakeRunner(list=SINK_INPUTS))
    vols = pw.app_volumes()
    assert abs(vols["firefox"] - 0.7) < 0.001   # loudest Firefox stream
    assert abs(vols["discord"] - 0.25) < 0.001  # matched on binary "Discord"
    assert vols["spotify"] is None              # not playing


def test_set_app_volume_sets_every_matching_stream():
    run = FakeRunner(list=SINK_INPUTS)
    PipeWire(APPS, run=run).set_app_volume("firefox", 0.5)
    sets = [c for c in run.calls if "set-sink-input-volume" in c]
    assert sets == [["pactl", "set-sink-input-volume", "4477", "50%"],
                    ["pactl", "set-sink-input-volume", "4480", "50%"]]


def test_toggle_mute_command():
    run = FakeRunner()
    PipeWire(run=run).toggle_mic_mute()
    assert run.calls == [["wpctl", "set-mute", "@DEFAULT_SOURCE@", "toggle"]]


def test_relevant_events():
    assert is_relevant_event("Event 'change' on source #4353")
    assert is_relevant_event("Event 'change' on sink #4354")
    assert is_relevant_event("Event 'new' on sink-input #4477")
    assert is_relevant_event("Event 'remove' on sink-input #4477")
    assert is_relevant_event("Event 'change' on server #-1")
    assert not is_relevant_event("Event 'new' on client #4485")
    assert not is_relevant_event("Event 'change' on card #4341")
    assert not is_relevant_event("Event 'change' on source-output #12")
