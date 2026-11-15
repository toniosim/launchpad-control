import importlib.util
import json
import queue
import random
import subprocess
import sys
from pathlib import Path

import pytest

from launchpad_control import config, grid
from launchpad_control.app import (BANK_ACTIVE, BANK_INACTIVE, CLAUDE_ASKING, CLAUDE_BLINK_S,
                                   CLAUDE_DONE, CLAUDE_FRAME_S, CLAUDE_WORKING, App)
from launchpad_control.config import ConfigError
from launchpad_control.events import AudioChanged, ButtonChange
from launchpad_control.fake_device import FakeDevice
from launchpad_control.integrations import claude
from launchpad_control.integrations.claude import (ClaudeListener, HookEvent, Sessions,
                                                   asks_user, find_predecessor, parse_hook,
                                                   status_after)
from launchpad_control.integrations.pipewire import AudioState

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def hook(event, sid="s1", **kw):
    return HookEvent(event, sid, **kw)


# ---- parsing + status ----

def test_parse_hook_keeps_known_fields():
    data = json.dumps({"hook_event_name": "PreToolUse", "session_id": "abc", "cwd": "/x",
                       "tool_name": "Bash", "tool_input": {"command": "ls"}}).encode()
    assert parse_hook(data) == HookEvent("PreToolUse", "abc", "/x", "Bash")


@pytest.mark.parametrize("data", [b"", b"nope", b"[]", b'{"session_id": "a"}',
                                  b'{"hook_event_name": "Stop", "session_id": 3}',
                                  b"\xff\xfe"])
def test_parse_hook_rejects_garbage(data):
    assert parse_hook(data) is None


@pytest.mark.parametrize("event, current, expected", [
    (hook("SessionStart"), None, claude.READY),
    (hook("SessionStart"), claude.WORKING, claude.WORKING),  # auto-compact mid-task
    (hook("UserPromptSubmit"), claude.NEEDS_INPUT, claude.WORKING),
    (hook("PreToolUse", tool="Bash"), claude.WORKING, claude.WORKING),
    (hook("PreToolUse", tool="AskUserQuestion"), claude.WORKING, claude.NEEDS_INPUT),
    (hook("PreToolUse", tool="ExitPlanMode"), claude.WORKING, claude.NEEDS_INPUT),
    (hook("PermissionRequest"), claude.WORKING, claude.NEEDS_INPUT),
    (hook("PostToolUse", tool="Bash"), claude.NEEDS_INPUT, claude.WORKING),
    (hook("Notification", notification="permission_prompt"), claude.WORKING,
     claude.NEEDS_INPUT),
    (hook("Notification", notification="idle_prompt"), claude.NEEDS_INPUT, claude.NEEDS_INPUT),
    (hook("Notification", message="Claude needs your permission to use Bash"),
     claude.WORKING, claude.NEEDS_INPUT),
    (hook("Notification", message="Claude is waiting for your input"), claude.READY,
     claude.READY),
    (hook("Stop"), claude.WORKING, claude.NEEDS_INPUT),
    (hook("SubagentStop"), claude.WORKING, claude.WORKING),
    (hook("SessionEnd"), claude.NEEDS_INPUT, None),
])
def test_status_after(event, current, expected):
    assert status_after(event, current) == expected


def test_sessions_keep_order_and_update_in_place():
    s = Sessions()
    s = s.apply(hook("SessionStart", "a", cwd="/a"), 0)
    s = s.apply(hook("UserPromptSubmit", "b"), 1)
    s = s.apply(hook("UserPromptSubmit", "a"), 2)
    assert [(x.id, x.status, x.cwd) for x in s.items] == \
        [("a", claude.WORKING, "/a"), ("b", claude.WORKING, "")]
    s = s.apply(hook("SessionEnd", "a"), 3)
    assert [x.id for x in s.items] == ["b"]


def test_sessions_prune_stale():
    s = Sessions().apply(hook("Stop", "a"), 0).apply(hook("Stop", "b"), 50)
    assert [x.id for x in s.prune(100, 60).items] == ["b"]


# ---- listener + hook script ----

def test_listener_receives_datagrams(tmp_path):
    got: "queue.Queue[HookEvent]" = queue.Queue()
    listener = ClaudeListener(tmp_path / "rt" / "claude.sock")
    listener.watch(got.put)
    try:
        import socket
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(b"junk", str(listener.path))
            s.sendto(json.dumps({"hook_event_name": "Stop", "session_id": "x"}).encode(),
                     str(listener.path))
        assert got.get(timeout=2) == HookEvent("Stop", "x")
    finally:
        listener.stop()
    assert not listener.path.exists()


def test_hook_script_forwards_and_stays_silent(tmp_path):
    got: "queue.Queue[HookEvent]" = queue.Queue()
    listener = ClaudeListener(tmp_path / "launchpad-control" / "claude.sock")
    listener.watch(got.put)
    payload = {"hook_event_name": "Notification", "session_id": "s", "cwd": "/p",
               "notification_type": "permission_prompt", "message": "m" * 1000,
               "transcript_path": "/t"}
    try:
        r = subprocess.run([sys.executable, "-I", "-S", str(SCRIPTS / "claude_hook.py")],
                           input=json.dumps(payload), capture_output=True, text=True,
                           env={"XDG_RUNTIME_DIR": str(tmp_path)}, timeout=10)
        assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
        assert got.get(timeout=2) == HookEvent("Notification", "s", "/p", "",
                                               "permission_prompt", "m" * 200, "/t")
    finally:
        listener.stop()


def test_hook_script_ignores_missing_daemon_and_bad_input(tmp_path):
    for stdin in ("not json", json.dumps({"hook_event_name": "Stop", "session_id": "s"})):
        r = subprocess.run([sys.executable, "-I", "-S", str(SCRIPTS / "claude_hook.py")],
                           input=stdin, capture_output=True, text=True,
                           env={"XDG_RUNTIME_DIR": str(tmp_path)}, timeout=10)
        assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def _installer():
    spec = importlib.util.spec_from_file_location("installer",
                                                  SCRIPTS / "install-claude-hooks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_installer_is_idempotent_and_keeps_other_hooks():
    inst = _installer()
    other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "mine.sh"}]}
    settings = {"model": "x", "hooks": {"PreToolUse": [other]}}
    once = inst.update(json.loads(json.dumps(settings)), True)
    twice = inst.update(json.loads(json.dumps(once)), True)
    assert once == twice
    assert once["hooks"]["PreToolUse"][0] == other
    assert set(once["hooks"]) == set(inst.EVENTS)
    assert inst.update(twice, False) == settings


# ---- config ----

def test_config_claude_section():
    assert config.parse({"claude": {}}).claude.stale_after == 7200
    assert config.parse({"claude": {"stale_after": 60}}).claude.stale_after == 60
    assert config.parse({}).claude is None
    with pytest.raises(ConfigError):
        config.parse({"claude": {"color": "red"}})


# ---- in the app ----

class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Audio:
    def __init__(self):
        self.calls = []

    def toggle_mic_mute(self):
        self.calls.append("mute")


LAYOUT = config.parse({"mic_mute": {"pad": [1, 8]}, "idle": {"after": 60, "mode": "life"},
                       "claude": {}})


def make_app(layout=LAYOUT):
    clock, dev, audio = Clock(), FakeDevice(), Audio()
    app = App(dev, audio, layout, clock=clock, rng=random.Random(1))
    dev.set_event_handler(app.step)
    dev.emit(AudioChanged(AudioState(mic_muted=True)))
    return app, dev, audio, clock


def to_claude(dev):
    dev.emit(ButtonChange(102, 127))
    dev.emit(ButtonChange(102, 0))


def row(dev, r):
    return [dev.leds.get(grid.idx(r, c)) for c in range(1, 9)]


def test_bank_buttons_show_active_bank():
    app, dev, _, _ = make_app()
    assert (dev.leds[101], dev.leds[102]) == (BANK_ACTIVE, BANK_INACTIVE)
    to_claude(dev)
    assert app.bank == "claude"
    assert (dev.leds[101], dev.leds[102]) == (BANK_INACTIVE, BANK_ACTIVE)
    assert grid.idx(1, 8) not in dev.leds  # main bank's mic pad is gone
    dev.emit(ButtonChange(101, 127))
    assert app.bank == "main" and dev.leds[grid.idx(1, 8)] == grid.RED


def test_no_bank_buttons_with_a_single_bank():
    app, dev, _, _ = make_app(config.parse({"mic_mute": {"pad": [1, 8]}}))
    assert dev.leds == {18: grid.RED}
    dev.emit(ButtonChange(102, 127))  # disabled bank: ignored
    assert app.bank == "main"


def test_pads_act_on_the_visible_bank_only():
    app, dev, audio, _ = make_app()
    to_claude(dev)
    dev.press(18)
    assert audio.calls == []


def test_sessions_render_as_rows():
    app, dev, _, clock = make_app()
    to_claude(dev)
    dev.emit(hook("UserPromptSubmit", "a"))
    dev.emit(hook("Stop", "b"))
    dev.emit(hook("PermissionRequest", "c"))
    dev.emit(hook("SessionStart", "d"))
    top, second, third, fourth = row(dev, 8), row(dev, 7), row(dev, 6), row(dev, 5)
    assert CLAUDE_WORKING in top  # the comet's head
    for r in (second, third):  # finished turn and permission prompt look the same
        assert len(set(r)) == 1 and r[0] is not None  # whole row, one pulse level
        assert r[0][1] > r[0][2]  # yellow-ish
    assert fourth[0] is not None and fourth[1:] == [None] * 7
    assert row(dev, 4) == [None] * 8


def test_working_comet_moves_with_time():
    app, dev, _, clock = make_app()
    to_claude(dev)
    dev.emit(hook("UserPromptSubmit", "a"))
    before = row(dev, 8)
    wait = app.seconds_until_tick()
    assert 0 < wait <= CLAUDE_FRAME_S + 0.01
    clock.t += wait
    app.step()
    assert row(dev, 8) != before


def test_no_animation_wakeups_when_nothing_moves():
    app, dev, _, clock = make_app(config.parse({"claude": {}}))  # no idle either
    dev.emit(hook("SessionStart", "a"))
    assert app.seconds_until_tick() == float("inf")
    to_claude(dev)
    assert app.seconds_until_tick() == float("inf")  # a ready session is static


def test_bank_button_blinks_while_a_session_needs_input():
    app, dev, _, clock = make_app()
    dev.emit(hook("UserPromptSubmit", "a"))
    assert dev.leds[102] == grid.scale(CLAUDE_WORKING, 0.35)
    dev.emit(hook("PermissionRequest", "a"))
    first = dev.leds[102]
    assert app.seconds_until_tick() <= CLAUDE_BLINK_S + 0.01
    clock.t += CLAUDE_BLINK_S
    app.step()
    assert {first, dev.leds[102]} == {CLAUDE_ASKING, grid.scale(CLAUDE_ASKING, 0.1)}


def test_tap_clears_finished_rows_but_not_working_ones():
    app, dev, _, _ = make_app()
    to_claude(dev)
    dev.emit(hook("UserPromptSubmit", "a"))
    dev.emit(hook("Stop", "b"))
    dev.press(grid.idx(8, 4))
    dev.press(grid.idx(7, 4))
    assert [s.id for s in app.claude.items] == ["a"]
    dev.press(grid.idx(3, 1))  # empty row: nothing
    assert len(app.claude.items) == 1


def test_no_idle_on_the_claude_bank_while_a_session_works():
    app, dev, _, clock = make_app()
    to_claude(dev)
    dev.emit(hook("UserPromptSubmit", "a"))
    clock.t += 1_000
    app.step()
    assert app.idle is None
    dev.emit(hook("Stop", "a"))  # work stopped: the idle timer starts now
    clock.t += 59
    app.step()
    assert app.idle is None
    clock.t += 1
    app.step()
    assert app.idle is not None


def test_idles_on_the_claude_bank_when_nothing_works():
    app, dev, _, clock = make_app()
    to_claude(dev)
    dev.emit(hook("Stop", "a"))
    clock.t += 60
    app.step()
    assert app.idle is not None
    assert dev.leds[102] in (CLAUDE_ASKING, grid.scale(CLAUDE_ASKING, 0.1))  # still visible


@pytest.mark.parametrize("event", ["UserPromptSubmit", "PermissionRequest"])
def test_session_needing_attention_wakes_idle_on_the_claude_bank(event):
    app, dev, _, clock = make_app()
    to_claude(dev)
    dev.emit(hook("Stop", "a"))
    clock.t += 60
    app.step()
    dev.emit(hook("UserPromptSubmit", "a"))  # the first turn ends below
    dev.emit(hook("Stop", "a"))
    clock.t += 60
    app.step()
    assert app.idle is not None
    dev.emit(hook(event, "b"))
    assert app.idle is None


def test_claude_events_dont_wake_idle_on_the_main_bank():
    app, dev, _, clock = make_app()
    clock.t += 60
    app.step()
    dev.emit(hook("UserPromptSubmit", "a"))
    dev.emit(hook("Stop", "a"))
    assert app.idle is not None


def test_bank_buttons_stay_lit_while_idle():
    app, dev, _, clock = make_app()
    clock.t += 60
    app.step()
    assert app.idle is not None
    assert dev.leds[101] == BANK_ACTIVE


def test_removing_claude_from_config_returns_to_main():
    app, dev, _, _ = make_app()
    to_claude(dev)
    app.layout = config.parse({"mic_mute": {"pad": [1, 8]}})
    app.step()
    assert app.bank == "main" and dev.leds == {18: grid.RED}


def test_stale_sessions_are_forgotten():
    app, dev, _, clock = make_app(config.parse({"claude": {"stale_after": 100}}))
    dev.emit(hook("UserPromptSubmit", "a"))
    clock.t += 100
    app.step()
    assert app.claude.items == ()


# ---- finished: asking or not ----

@pytest.mark.parametrize("reply, asks", [
    ("Committed as 5f01ee3.", False),
    ("Done.\n\nWant me to commit?", True),
    ("Want me to commit?\n\nOtherwise it's all done.", False),  # only the last paragraph
    ("Ran the tests.\n\nShould I deploy it as well", True),
    ("Fixed.\nNeeds input: which port?", True),
    ("", False),
])
def test_asks_user(reply, asks):
    assert asks_user(reply) == asks


def test_stop_status_follows_the_reply():
    assert status_after(hook("Stop", last_message="All done."), claude.WORKING) == claude.DONE
    assert status_after(hook("Stop", last_message="Commit?"), claude.WORKING) == \
        claude.NEEDS_INPUT
    assert status_after(hook("Stop"), claude.WORKING) == claude.NEEDS_INPUT  # no text sent


def test_finished_row_is_calm_and_doesnt_wake_idle():
    app, dev, _, clock = make_app()
    to_claude(dev)
    dev.emit(hook("Stop", "a", last_message="All done."))
    assert row(dev, 8)[0] == CLAUDE_DONE
    assert dev.leds[102] == BANK_ACTIVE  # no blinking
    clock.t += 60
    app.step()
    assert app.idle is not None
    dev.emit(hook("UserPromptSubmit", "b"))
    dev.emit(hook("Stop", "b", last_message="Finished."))
    clock.t += 60
    app.step()
    assert app.idle is not None
    dev.emit(hook("Stop", "a", last_message="Finished too."))
    assert app.idle is not None


# ---- a session reopened under a new id ----

def transcript(tmp_path, *ids):
    f = tmp_path / "new.jsonl"
    f.write_text("\n".join([json.dumps({"type": "mode", "sessionId": "new"})]
                           + [json.dumps({"session_id": i, "text": "x"}) for i in ids]))
    return str(f)


def test_find_predecessor(tmp_path):
    path = transcript(tmp_path, "old", "new")
    assert find_predecessor(path, {"old", "other"}) == "old"
    assert find_predecessor(path, {"other"}) is None
    assert find_predecessor(str(tmp_path / "missing"), {"old"}) is None
    assert find_predecessor("", {"old"}) is None


def test_sessions_rename_keeps_position():
    s = Sessions().apply(hook("Stop", "a"), 0).apply(hook("UserPromptSubmit", "b"), 1)
    s = s.rename("a", "a2")
    assert [(x.id, x.status) for x in s.items] == [("a2", claude.NEEDS_INPUT),
                                                    ("b", claude.WORKING)]


def test_reopened_session_takes_over_its_row(tmp_path):
    app, dev, _, _ = make_app()
    dev.emit(hook("UserPromptSubmit", "first"))
    dev.emit(hook("Stop", "old", last_message="Done."))
    dev.emit(hook("SessionStart", "new", source="resume",
                  transcript=transcript(tmp_path, "old")))
    assert [(x.id, x.status) for x in app.claude.items] == [("first", claude.WORKING),
                                                             ("new", claude.DONE)]
    dev.emit(hook("SessionStart", "new", source="resume",  # again: nothing changes
                  transcript=transcript(tmp_path, "first")))
    assert [x.id for x in app.claude.items] == ["first", "new"]
