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
- ✅ **Runs in the background** as a systemd user service, survives unplug/replug, one-command updates (see below).
- ⏭️ Next: map the edge buttons (CCs), page system.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/launchpad-control        # -v logs every event; Ctrl+C restores the device
.venv/bin/pytest                   # unit tests, no hardware needed
.venv/bin/python scripts/lp_hello.py   # hardware smoke test
```

## Running in the background

A systemd **user** service starts the app at login. It waits for the Launchpad if it isn't
plugged in, and recovers if it's unplugged and replugged.

```bash
scripts/install-service.sh   # one-time: install, enable at login, start (also links lpctl into ~/.local/bin)
lpctl status                 # status | start | stop | restart | enable | disable | logs | update | dev
lpctl stop                   # stop now; the pad is cleared and returned to Live mode
lpctl disable                # stop now and don't start at login
lpctl logs                   # follow the journal
lpctl update                 # after changing code: refresh deps if needed, run tests, restart
lpctl dev                    # run in the foreground with -v; the service resumes when you quit
```

The service runs from this checkout (editable install), so editing code and running `lpctl update`
is the whole deploy. If the tests or config check fail, the running service is left untouched. If
you stopped the service on purpose, `update` won't start it. Config edits don't need an update:
they reload within a second. `scripts/install-service.sh --uninstall` removes everything.

A config error at startup exits with code 2, which the unit treats as "don't retry"; check
`lpctl logs`. Don't run a second copy by hand while the service is up (both would act on every
press); use `lpctl dev` instead.

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
