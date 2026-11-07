# Feature ideas / backlog

Original goals from Tonio are marked ⭐. Rough priority: **P1** = build first, **P2** = next, **P3** = fun/later.

## Audio & voice
- ⭐ **P1 Mic mute with state-synced LED**: red when muted, regardless of where it was muted. Smallest end-to-end feature that covers the whole pattern (input → system action → LED shows real state). **Build first.**
- ⭐ **P1 Volume control**: master plus per-app. A column of 8 pads works as a vertical fader for one app (Discord, game, browser, Spotify), with lit height = level. Pressure could give fine adjustment.
- ⭐ **P1 Soundboard into the mic channel**: via a PipeWire virtual source (mic + soundboard mix).
  - Velocity → playback volume
  - Hold to loop
  - Choke groups (a new clip cuts the previous one)
  - Color-coded categories; edge buttons page through banks
  - Pad pulses while its clip is playing
  - Stop-all button
- **P2 Output device switching**: one pad per device (headphones, speakers, HDMI), lit = active
- **P3 Live voice effects**: toggle PipeWire filter chains (pitch shift, reverb, radio/walkie-talkie)

## Discord
- ✅ **P1 Mute / deafen** with LED state (local RPC)
- **P2** Flash a pad on mentions/DMs (RPC `NOTIFICATION_CREATE`, needs the `rpc.notifications.read` scope)
- **P2** Speaking indicators / per-user volume for people in the current voice channel (RPC)
- **P3** Push-to-talk, camera/screen-share toggles (RPC where supported, else uinput keybinds)

## Status display (LEDs as a dashboard)
- **P2 Home server grid**: one pad per container/service, green = healthy, red = down; hold a pad to restart the service
- **P2 Home Assistant**: lights and scenes, pad color = actual light color
- **P2 Claude Code / agent stack alerts**: a pad pulses when a session needs approval or finishes; tap to focus that terminal
- **P2 Test/CI status**: a row of pads shows pipeline pass/fail; tap to run a local test suite
- **P3 Notifications**: meeting countdown, Pomodoro timer that drains the grid

## Workflow macros
- **P2 Media controls** via `playerctl` (play/pause/skip), color by active player
- **P2 App launcher / COSMIC workspace switching / window tiling** (uinput or ydotool; xdotool won't work on Wayland)
- **P2 OBS**: scene switching, record/stream toggle with live-state LEDs (obs-websocket)
- **P3 Clipboard/snippet bank**: frequently used strings, commands, test data

## Just for fun
- **P3 Ambient/idle mode**: audio visualizer from system output, clock, Conway's Game of Life
- **P3 V-Sonas on the grid**: pet mood/hunger as colors, tap to feed, tiny 8×8 minigames

## Infrastructure (not features, but needed)
- Edge-button CC map (`docs/BUTTON_MAP.md`)
- Page/mode system on edge buttons
- ~~Config file for mappings~~ done: TOML, `~/.config/launchpad-control/config.toml`, live reload
- Frame buffer + diffed batched SysEx
- ~~Hot-plug reconnect~~ done: `app.serve` waits for the device, repaints from carried state on replug
- ~~systemd user service~~ done: `systemd/`, `scripts/install-service.sh`, `scripts/lpctl`, `scripts/update.sh`
- Fake device for tests without hardware
