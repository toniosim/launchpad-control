"""Discord voice controls (mute / deafen) over Discord's local RPC socket.

The Discord desktop client listens on a Unix socket (`discord-ipc-N`). Programs on the
same machine can connect, prove who they are with an OAuth token, then send commands and
subscribe to events. Voice control needs the restricted `rpc` scopes, which Discord only
grants to an application's owner and testers, so the user registers their own app (the
Application ID goes in the config, the client secret in a file next to it).

First run: Discord shows an "Authorize" popup. The resulting token is saved and refreshed
automatically, so the popup doesn't come back unless the token is revoked.

State (connected, muted, deafened) is pushed to `watch()`'s callback whenever it changes,
including changes made in Discord itself.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import struct
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

log = logging.getLogger(__name__)

OP_HANDSHAKE, OP_FRAME, OP_CLOSE, OP_PING, OP_PONG = range(5)
SCOPES = ("rpc", "rpc.voice.read", "rpc.voice.write")
REDIRECT_URI = "http://localhost"  # must also be listed under OAuth2 -> Redirects
TOKEN_URL = "https://discord.com/api/oauth2/token"
USER_AGENT = "launchpad-control/0.1"
RETRY_S = 5.0          # Discord not running / connection lost
AUTH_RETRY_S = 600.0   # authorization declined or failed: don't pop the dialog again soon
_FLATPAK_IDS = ("com.discordapp.Discord", "com.discordapp.DiscordCanary")

Payload = dict[str, Any]
TokenRequest = Callable[[Mapping[str, str]], Payload]


@dataclass(frozen=True)
class DiscordState:
    """None means unknown (e.g. not connected)."""
    connected: bool = False
    muted: bool | None = None
    deafened: bool | None = None


class RPCError(Exception):
    """Discord answered a command with an error."""

    def __init__(self, code: Any, message: str) -> None:
        super().__init__(f"{message} (code {code})")
        self.code = code


class OAuthError(Exception):
    """Discord's token endpoint refused a request (bad code, revoked token, wrong secret...)."""


class SetupError(Exception):
    """Something the user has to fix (e.g. missing secret). Retrying won't help."""


class AuthFailed(Exception):
    """The Authorize step was declined or couldn't complete. Back off before asking again."""


# ---- wire format ---------------------------------------------------------------
# Each message: little-endian uint32 opcode, uint32 length, then that many bytes of JSON.

def encode(op: int, payload: Payload) -> bytes:
    data = json.dumps(payload).encode()
    return struct.pack("<II", op, len(data)) + data


class Connection:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self._send_lock = threading.Lock()

    def send(self, op: int, payload: Payload) -> None:
        with self._send_lock:
            self.sock.sendall(encode(op, payload))

    def recv(self) -> tuple[int, Payload]:
        op, length = struct.unpack("<II", self._recv_exact(8))
        return op, json.loads(self._recv_exact(length) or b"{}")

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)  # wakes a reader blocked in recv()
        except OSError:
            pass
        self.sock.close()

    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Discord closed the connection")
            buf += chunk
        return bytes(buf)


# ---- finding the socket ----------------------------------------------------------

def socket_candidates(env: Mapping[str, str] = os.environ) -> list[Path]:
    """Where Discord's socket can live: native, Flatpak and Snap installs."""
    dirs: list[Path] = []
    for var in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP"):
        base = env.get(var)
        if not base:
            continue
        dirs.append(Path(base))
        dirs += [Path(base) / "app" / app_id for app_id in _FLATPAK_IDS]
        dirs.append(Path(base) / "snap.discord")
    dirs.append(Path("/tmp"))
    unique = list(dict.fromkeys(dirs))
    return [d / f"discord-ipc-{i}" for d in unique for i in range(10)]


def connect_socket() -> socket.socket:
    """Connect to the first Discord socket that answers. OSError if Discord isn't running."""
    last: OSError = FileNotFoundError("Discord isn't running (no discord-ipc socket)")
    for path in socket_candidates():
        if not path.exists():
            continue
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(str(path))
            return sock
        except OSError as e:
            sock.close()
            last = e  # stale socket left behind by a crashed client
    raise last


# ---- OAuth ---------------------------------------------------------------------------

def http_token_request(form: Mapping[str, str]) -> Payload:
    req = urllib.request.Request(
        TOKEN_URL, data=urllib.parse.urlencode(form).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        raise OAuthError(f"HTTP {e.code}: {body}") from None


def load_token(path: Path) -> Payload | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("access_token") else None


def save_token(path: Path, token: Payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(token, f)
    os.replace(tmp, path)


# ---- the client ------------------------------------------------------------------------

class Discord:
    def __init__(self, client_id: str, secret_path: Path, token_path: Path, *,
                 connect: Callable[[], socket.socket] = connect_socket,
                 token_request: TokenRequest = http_token_request) -> None:
        self.client_id = client_id
        self.secret_path = secret_path
        self.token_path = token_path
        self._connect = connect
        self._token_request = token_request
        self._state = DiscordState()
        self._on_change: Callable[[DiscordState], None] = lambda s: None
        self._conn: Connection | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()

    # ---- public API (called from the main thread) ----
    @property
    def state(self) -> DiscordState:
        return self._state

    def toggle_mute(self) -> None:
        s = self._state
        # Deafened counts as muted, so the first press while deafened un-mutes (and
        # un-deafens), like clicking the mic icon in Discord.
        self._command("SET_VOICE_SETTINGS", {"mute": not (s.muted or s.deafened)})

    def toggle_deafen(self) -> None:
        self._command("SET_VOICE_SETTINGS", {"deaf": not self._state.deafened})

    def watch(self, on_change: Callable[[DiscordState], None]) -> None:
        """Connect in the background and keep reconnecting. on_change gets every new state."""
        self._on_change = on_change
        on_change(self._state)
        threading.Thread(target=self._run, daemon=True, name="discord").start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self._conn is not None:
                self._conn.close()

    # ---- background thread ----
    def _run(self) -> None:
        quiet = set()  # messages already logged, so waiting doesn't spam the journal

        def once(level: int, msg: str, *args: Any) -> None:
            key = msg % args
            if key not in quiet:
                quiet.add(key)
                log.log(level, msg, *args)

        while not self._stop.is_set():
            delay = RETRY_S
            try:
                secret = self._read_secret()
                conn = Connection(self._connect())
            except SetupError as e:
                once(logging.WARNING, "%s", e)
                self._stop.wait(RETRY_S)
                continue
            except OSError as e:
                once(logging.INFO, "Waiting for Discord (%s)", e)
                self._stop.wait(RETRY_S)
                continue
            quiet.clear()
            with self._lock:
                self._conn = conn
            if self._stop.is_set():  # stop() ran between connect and here
                conn.close()
                break
            try:
                self._session(conn, secret)
            except AuthFailed as e:
                log.error("Discord authorization failed: %s. Will ask again in %d minutes "
                          "(or run: lpctl restart)", e, AUTH_RETRY_S // 60)
                delay = AUTH_RETRY_S
            except (OSError, ValueError, struct.error, RPCError, OAuthError) as e:
                if not self._stop.is_set():
                    log.warning("Discord connection lost: %s", e)
            finally:
                with self._lock:
                    self._conn = None
                conn.close()
                self._set_state(DiscordState())
            self._stop.wait(delay)

    def _session(self, conn: Connection, secret: str) -> None:
        conn.send(OP_HANDSHAKE, {"v": 1, "client_id": self.client_id})
        while True:
            ready = self._read(conn)
            if ready.get("evt") == "READY":
                break
        user = (ready.get("data") or {}).get("user") or {}
        log.info("Connected to Discord (%s)", user.get("username", "unknown user"))

        self._authenticate(conn, secret)
        self._request(conn, "SUBSCRIBE", evt="VOICE_SETTINGS_UPDATE")
        self._apply_voice(self._request(conn, "GET_VOICE_SETTINGS"))
        log.info("Discord voice controls ready")
        while True:
            self._dispatch(self._read(conn))

    def _authenticate(self, conn: Connection, secret: str) -> None:
        token = load_token(self.token_path)
        if token is not None:
            if self._try_authenticate(conn, token):
                return
            if token.get("refresh_token"):
                token = self._refresh(token, secret)
                if token is not None and self._try_authenticate(conn, token):
                    return
        token = self._authorize(conn, secret)
        self._request(conn, "AUTHENTICATE", {"access_token": token["access_token"]})

    def _try_authenticate(self, conn: Connection, token: Payload) -> bool:
        try:
            self._request(conn, "AUTHENTICATE", {"access_token": token["access_token"]})
            return True
        except RPCError as e:
            log.info("Saved Discord token not accepted (%s)", e)
            return False

    def _refresh(self, token: Payload, secret: str) -> Payload | None:
        try:
            new = self._token_request({
                "grant_type": "refresh_token", "refresh_token": token["refresh_token"],
                "client_id": self.client_id, "client_secret": secret})
        except OAuthError as e:
            log.info("Couldn't refresh the Discord token (%s); asking again", e)
            return None
        save_token(self.token_path, new)
        log.info("Discord token refreshed")
        return new

    def _authorize(self, conn: Connection, secret: str) -> Payload:
        log.warning("Asking Discord for permission: click Authorize in the Discord window")
        try:
            data = self._request(conn, "AUTHORIZE",
                                 {"client_id": self.client_id, "scopes": list(SCOPES)})
        except RPCError as e:
            raise AuthFailed(f"Discord said: {e}") from None
        try:
            token = self._token_request({
                "grant_type": "authorization_code", "code": data["code"],
                "redirect_uri": REDIRECT_URI,
                "client_id": self.client_id, "client_secret": secret})
        except (OAuthError, OSError, KeyError) as e:
            raise AuthFailed(f"token exchange failed: {e} (check the client secret and that "
                             f"{REDIRECT_URI} is listed under OAuth2 -> Redirects)") from None
        save_token(self.token_path, token)
        log.info("Discord authorized; token saved to %s", self.token_path)
        return token

    # ---- messaging ----
    def _read(self, conn: Connection) -> Payload:
        """Next command/event frame, answering pings and surfacing a close."""
        while True:
            op, payload = conn.recv()
            if op == OP_FRAME:
                return payload
            if op == OP_PING:
                conn.send(OP_PONG, payload)
            elif op == OP_CLOSE:
                raise ConnectionError(f"Discord closed the connection: "
                                      f"{payload.get('message')} (code {payload.get('code')})")

    def _request(self, conn: Connection, cmd: str, args: Payload | None = None,
                 evt: str | None = None) -> Payload:
        """Send a command and wait for its reply. Events arriving meanwhile are handled."""
        nonce = str(uuid.uuid4())
        msg: Payload = {"cmd": cmd, "args": args or {}, "nonce": nonce}
        if evt:
            msg["evt"] = evt
        conn.send(OP_FRAME, msg)
        while True:
            payload = self._read(conn)
            if payload.get("nonce") != nonce:
                self._dispatch(payload)
                continue
            data = payload.get("data") or {}
            if payload.get("evt") == "ERROR":
                raise RPCError(data.get("code"), data.get("message", "unknown error"))
            return data

    def _command(self, cmd: str, args: Payload) -> None:
        """Fire-and-forget from the main thread. The reply/event updates the state."""
        with self._lock:
            conn = self._conn
        if conn is None or not self._state.connected:
            log.info("Discord isn't connected; ignoring %s", cmd)
            return
        try:
            conn.send(OP_FRAME, {"cmd": cmd, "args": args, "nonce": str(uuid.uuid4())})
        except OSError as e:
            log.warning("Couldn't send %s to Discord: %s", cmd, e)

    def _dispatch(self, payload: Payload) -> None:
        cmd, evt, data = payload.get("cmd"), payload.get("evt"), payload.get("data") or {}
        if evt == "ERROR":
            log.warning("Discord rejected %s: %s", cmd, data.get("message"))
        elif evt == "VOICE_SETTINGS_UPDATE" or cmd in ("GET_VOICE_SETTINGS",
                                                       "SET_VOICE_SETTINGS"):
            self._apply_voice(data)

    def _apply_voice(self, data: Payload) -> None:
        if "mute" in data or "deaf" in data:
            s = self._state
            self._set_state(DiscordState(True, bool(data.get("mute", s.muted)),
                                         bool(data.get("deaf", s.deafened))))

    def _set_state(self, state: DiscordState) -> None:
        if state != self._state:
            self._state = state
            self._on_change(state)

    def _read_secret(self) -> str:
        try:
            secret = self.secret_path.read_text().strip()
        except OSError:
            secret = ""
        if not secret:
            raise SetupError(f"Discord is configured but the client secret is missing: put it "
                             f"in {self.secret_path} (see README, Discord section)")
        return secret
