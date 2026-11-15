# launchpad-control

Custom desktop control software for a **Novation Launchpad Pro [MK3]** on **Pop!_OS 24 (COSMIC, Wayland, PipeWire)**.

Turns the 8×8 RGB pad grid into a control surface for the desktop: per-app volume, mic mute, a soundboard that plays into the mic, Discord and media controls, home server status, notifications, and more. The point is two-way control: pads trigger actions, and LEDs show the real state of the system.

## Status

- ✅ `scripts/lp_hello.py` works on hardware: enters Programmer mode, sets RGB per pad, lights pads on press (velocity → brightness), logs all incoming MIDI, restores Live mode on exit.
- ✅ **Mic mute** (pad 88, top-right): toggles system mic mute; red = muted, dim green = live.
- ✅ **Volume faders**: column 1 = master (green), column 2 = Firefox (orange), column 3 = Discord (purple). Press a pad to set the level (row × 12.5%); press the bottom pad again to go to 0. An app that isn't playing shows only a dim bottom pad.
- ✅ **Active-app fader** (column 4, cyan): controls whatever else is playing (e.g. a game): the newest audio stream not claimed by another fader, skipping notification sounds, paused streams and an ignore list. The log says which app it's controlling.
- ✅ **Discord mute / deafen** (pads 28 and 38, below mic mute): red = muted/deafened, dim blurple = live, dim grey = Discord not running. Follows changes made in Discord too. One-time setup below.
- ✅ **Media controls** (bottom row, pads 16 / 17 / 18, blue): previous, play/pause, next for the active player via `playerctl` (MPRIS). The LEDs follow the real player state: all three blue while a player exists, play/pause dims while paused, all grey when no player is running (event driven via `playerctl --follow status`, no polling). With several players running, `playerctl` picks one; `playerctld` makes it the most recently active. Configure under `[media]`.
- ✅ **Idle mode**: after 5 minutes without a press, the grid plays an animation picked at random: Conway's Game of Life (8×8 torus, colour = cell age, reseeds when it dies out or loops) or Matrix-style rain. Any press wakes it and does nothing else. On the Claude bank it waits while a session is working, and wakes when a session starts working or needs you. Set `[idle] after`/`mode`/`interval` in the config, or remove the section to disable.
- ✅ **Banks**: the row of buttons just below the grid switches the whole grid between views. Button 1 = main (everything above), button 2 = Claude Code. The lit button is the active bank. Only shown when more than one bank is enabled.
- ✅ **Claude Code bank**: one row per running Claude Code session (oldest on top), fed by Claude Code hooks: orange comet = working, pulsing yellow = needs you (permission prompt, question, or its reply ends by asking you something), green = finished with nothing asked. The bank button turns orange while any session works and blinks yellow when one needs you, from any bank. Setup below.
- All LEDs follow the real PipeWire state, including changes made elsewhere (keyboard, panel, pavucontrol).
- ✅ **Config file**: layout, colours and app matching live in TOML (see below), reloaded live.
- ✅ **Runs in the background** as a systemd user service, survives unplug/replug, one-command updates (see below).
- ⏭️ Next: map the other edge buttons (`docs/BUTTON_MAP.md`).

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

## Suspend / resume and boot

The kernel log (`journalctl -k`) shows two separate ways the pad gets lost around sleep and boot:

- **Wake from suspend**: the kernel resets the pad in place (no unplug), so nothing looks wrong, but it comes back in the wrong mode with stale LEDs. The service notices the suspend (`CLOCK_BOOTTIME` vs `CLOCK_MONOTONIC`), waits 3s for USB to settle, then reopens the device, re-enters Programmer mode and repaints. No setup needed.
- **Stuck unconfigured** (`usb 1-4: can't set config #1, error -32`): on every cold boot here, and now and then after resume, the kernel finds the pad but its firmware refuses to be configured, so it has no MIDI ports. **Software can't fix this**: USB resets and re-configuring were tried and none worked (0 of 6), and a reset can even cause it on a healthy pad. Only a power loss, i.e. unplugging it, clears it. The service just logs a warning saying so after 6s.

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

## Discord

Mute and deafen go through Discord's local RPC socket (works with the official client, native
or Flatpak; not Vesktop/WebCord). Discord only allows voice control for apps you own, so you
register your own app once:

1. Go to <https://discord.com/developers/applications>, click **New Application**, and name it
   (e.g. "Launchpad").
2. Copy its **Application ID** into the config (the packaged default already has one):
   ```toml
   [discord]
   client_id = "1568435092655837295"
   mute_pad = [2, 8]
   deafen_pad = [3, 8]
   ```
3. Under **OAuth2**: add `http://localhost` to **Redirects** and save. Then click **Reset Secret**
   and save the secret to a file (it never goes in the config or the repo):
   ```bash
   mkdir -p ~/.config/launchpad-control
   printf '%s\n' 'PASTE_SECRET_HERE' > ~/.config/launchpad-control/discord-secret
   chmod 600 ~/.config/launchpad-control/discord-secret
   ```
4. `lpctl restart`, then click **Authorize** in the popup Discord shows. The token is saved in
   `~/.config/launchpad-control/discord-token.json` and refreshed automatically, so this happens
   once.

If Discord isn't running, the pads are dim grey and the app reconnects when it starts. If you
decline the popup (or the setup is wrong) it asks again after 10 minutes or on `lpctl restart`;
`lpctl logs` says what went wrong. To revoke access, remove the app under Discord → User Settings →
Authorized Apps and delete `discord-token.json`.

## Claude Code

The Claude bank shows what your Claude Code sessions are doing. Claude Code
[hooks](https://docs.claude.com/en/docs/claude-code/hooks) run `scripts/claude_hook.py` on
session start/end, prompt submit, tool use, permission requests, notifications and stop. It
forwards a few fields (event, session id, cwd, tool name) as one datagram to
`$XDG_RUNTIME_DIR/launchpad-control/claude.sock` and exits (about 20 ms, stdlib only, never
prints, always exits 0, so it can't slow down or break Claude Code even with the service stopped).

```bash
scripts/install-claude-hooks.py              # adds the hooks to ~/.claude/settings.json (saves a .bak)
scripts/install-claude-hooks.py --uninstall  # removes them again
```

Running it again doesn't duplicate anything, and your other hooks are kept. New Claude Code
sessions pick the hooks up (already-running ones did too, in testing). Enable the bank with a `[claude]` section in the config (the
packaged default has one).

| Row looks like | Meaning | Hooks |
|---|---|---|
| orange comet sweeping across | working | `UserPromptSubmit`, `PostToolUse` |
| whole row pulsing yellow | needs you: permission prompt, question, or a reply whose last paragraph asks something (`?`, "want me to", "should I") | `PermissionRequest`, `Notification` (permission), `PreToolUse` for `AskUserQuestion` / `ExitPlanMode`, `Stop` |
| green first pad, dim green rest | finished, nothing asked (doesn't blink the bank button or wake idle) | `Stop` |
| dim white first pad | started, no prompt yet | `SessionStart` |

Reopening a conversation from Claude Code's session list can give it a new session id; the
new session takes over the old row (found by the old id inside its transcript). A row disappears on `SessionEnd`, after `[claude] stale_after` seconds without any hook (default
2 h, for sessions that were killed), or when you tap it (any status but working).

## Docs

- [`docs/HANDOFF.md`](docs/HANDOFF.md): project context, decisions, hardware/protocol reference, Linux integration notes, open questions
- [`docs/IDEAS.md`](docs/IDEAS.md): feature backlog, roughly prioritized
- [`CLAUDE.md`](CLAUDE.md): working instructions for Claude Code sessions in this repo
