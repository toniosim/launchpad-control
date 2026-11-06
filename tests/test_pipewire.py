import subprocess

from launchpad_control.integrations.pipewire import MicMute, is_relevant_event


def fixed(output):
    calls = []

    def run(cmd):
        calls.append(cmd)
        return output
    return run, calls


def test_is_muted_parses_pactl():
    assert MicMute(fixed("Mute: yes\n")[0]).is_muted() is True
    assert MicMute(fixed("Mute: no\n")[0]).is_muted() is False
    assert MicMute(fixed("garbage")[0]).is_muted() is None


def test_is_muted_handles_failure():
    def boom(cmd):
        raise subprocess.CalledProcessError(1, cmd)
    assert MicMute(boom).is_muted() is None


def test_toggle_command():
    run, calls = fixed("")
    MicMute(run).toggle()
    assert calls == [["wpctl", "set-mute", "@DEFAULT_SOURCE@", "toggle"]]


def test_relevant_events():
    assert is_relevant_event("Event 'change' on source #4353")
    assert is_relevant_event("Event 'change' on server #-1")
    assert not is_relevant_event("Event 'new' on client #4485")
    assert not is_relevant_event("Event 'change' on card #4341")
    assert not is_relevant_event("Event 'change' on source-output #12")
