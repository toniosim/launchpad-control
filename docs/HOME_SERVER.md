# Home server integration (on hold)

**Status:** on hold since 2026-11-14 while Tonio researches the options. Nothing is built yet. This file records the plan, the options considered, and the decisions still open, so the work can resume without going over it all again.

Backlog entries: "P2 Home server grid" and "P2 Home Assistant" in `IDEAS.md`.

## Goal

Show the home server's containers on the Launchpad: one pad per container or service, green = healthy, red = down. Hold a pad to restart that service. As everywhere else in this project, the LEDs show **real state** (what Docker reports), not button history.

## Setup

- **Home server:** a separate laptop on the same LAN, running Docker containers.
- Already running on it: **Home Assistant** (used only for lights so far) and **Dockhand** (container manager, which already shows container health).
- **Desktop:** runs launchpad-control. The daemon **stays on the desktop**: the Launchpad's USB, PipeWire and Wayland input are all local. The server only has to answer "what state are my containers in?" and "restart X", over the LAN.

```
 Desktop (launchpad-control)               Home server laptop
 ┌──────────────────────────┐   LAN     ┌──────────────────────────┐
 │ integrations/<source>.py │◄─status───│ Dockhand / agent / HA    │
 │  → state → server bank   │──restart─►│  (reads Docker)          │
 └──────────────────────────┘           └──────────────────────────┘
```

## Resource cost (checked, not a concern)

If it's built on **events, not polling**, the cost is negligible on both machines:
- **Desktop:** one background thread waiting on a socket, the same pattern as `integrations/claude.py`. About 0% CPU and a few MB of RAM. It wakes only when a container changes state.
- **Server:** a small idle Python agent container uses about 30–60 MB of RAM and about 0% CPU. Streaming `docker events` is cheap because Docker pushes changes as they happen.
- **Network:** a few hundred bytes per event.
- **Avoid:** live CPU/RAM stats per container (`docker stats`, or `monitor_docker`'s default sensors). Those make Docker sample every container all the time, which is real CPU on a laptop. Up/down/health status is effectively free.

## Options

### A. Integrate with Dockhand (leading candidate, needs research)

Dockhand already tracks exactly what we want: whether each container is healthy.

- **For:** nothing new to deploy or maintain on the server. It reuses a tool that's already running and already has Docker access. Its view of "healthy" would match what Tonio sees in Dockhand's UI.
- **Against / unknown:** we haven't looked at Dockhand's API yet. Still to research:
  - Does it have a documented HTTP API? How does it authenticate (API token or session)?
  - Does it **push** changes (WebSocket, SSE, webhooks)? Or does the desktop have to poll? Polling a status endpoint every few seconds is fine; it's just less immediate.
  - Can the API restart a container? Can a token be limited to read-only, or to restart only?
  - Is the API stable, or internal to the UI and likely to change between versions?

### B. Small custom agent container (fallback, fully under our control)

About 100 lines of Python (FastAPI/aiohttp) running next to Dockhand, with the Docker socket mounted:
- `GET /status`: every container with its state and health
- `GET /events`: live stream fed by `docker events`, so pads change instantly instead of on a timer (fits the event-based change detection idea in `IDEAS.md`)
- `POST /restart/{name}`: shared token, and only containers on an allowlist

- **For:** instant updates, an API we design and that won't change on us, and the smallest possible permissions: the desktop can only read status and restart the containers we've allowed.
- **Against:** one more container to deploy and keep updated, and it holds the Docker socket (root-equivalent on the server) itself. Deploying it should be easy: Docker Compose, possibly via the same GitHub Actions runner setup used for other projects.

### C. Through Home Assistant (possible, not preferred right now)

Containers only appear in HA if an integration puts them there. Usually that's `monitor_docker` from HACS, which creates a `sensor.` (state) and a `switch.` (start/stop) per container. **They aren't in HA today.** The desktop would then use HA's WebSocket API, which pushes state changes, with a long-lived access token.

- **For:** one connection to HA would also cover lights, scenes and sensors on the Launchpad later, and the containers would show up in HA dashboards and automations too.
- **Against:** HA needs the Docker socket, which is **root-level control of the server**. Tonio isn't comfortable with that. `monitor_docker` also **polls** on an interval and collects stats by default, which you'd turn off. HA light status on the Launchpad isn't a confirmed want yet either, so the "one connection for everything" advantage may not matter.
- Revisit if Tonio's HA exploration turns up things he wants on the Launchpad.

### Also considered, not recommended

- **Docker over SSH** (`docker -H ssh://server`): needs no server-side code, but whoever holds the SSH key has full root, and the connection breaks whenever the laptop sleeps.
- **Docker socket proxy** (`tecnativa/docker-socket-proxy`): a filtered Docker API that allows reads and only POST restart. It's a reasonable middle ground, but option B is about as easy and gives a cleaner API. **Never** expose the raw Docker port (2375) on the LAN.

## Decisions to make

1. **Data source:** Dockhand (A) or a custom agent (B)? → Decide after researching Dockhand's API (the questions under A). If Dockhand has a usable, token-authenticated API, use it. If not, build B.
2. **Restart from the pad:** do we want it at all, or status only? If yes, which containers are allowed? A restart allowlist in config is planned either way.
3. **Push or poll:** depends on the source. With polling, pick an interval (5–10 s is plenty).
4. **Home Assistant:** separate decision, not blocking. Revisit after exploring HA. If it happens, it gets its own `integrations/homeassistant.py` regardless of where container status comes from.

## Desktop-side plan (the same whichever source wins)

- `integrations/<source>.py` holds the connection in a background thread and publishes state. It follows the pattern of `claude.py`, and is testable with a fake source, without the server.
- A new **server bank** maps containers to pads in config (TOML), e.g. `[homeserver] pads = { plex = 11, jellyfin = 12 }`.
- **Server unreachable** (laptop asleep or rebooting): show a dim/grey grid, never stale green, and reconnect with backoff.
- **Restart** on hold, not tap. The pad pulses while the restart runs and goes green when the source reports the container healthy again.
- Secrets (API token) go in a file under `~/.config/launchpad-control/`, like `discord-secret`, never in the config itself.
