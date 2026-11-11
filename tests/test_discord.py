"""The Discord RPC client against a fake Discord speaking the real wire format."""
import json
import os
import queue
import socket
import stat
import threading

import pytest

from launchpad_control.integrations import discord as discord_module
from launchpad_control.integrations.discord import (
    OP_CLOSE, OP_FRAME, OP_HANDSHAKE, OP_PING, OP_PONG, Connection, Discord, DiscordState,
    OAuthError, socket_candidates)

CLIENT_ID = "1568435092655837295"
TIMEOUT = 2.0


class FakeDiscord:
    """One Discord client connection. Answers commands like the real client does."""

    def __init__(self, valid_tokens=("good",), authorize_code="the-code",
                 mute=False, deaf=False, ping_first=False):
        self.valid_tokens = set(valid_tokens)
        self.authorize_code = authorize_code  # None = user clicks Cancel
        self.voice = {"mute": mute, "deaf": deaf}
        self.ping_first = ping_first
        self.received = []
        self.pong = None
        client, self._sock = socket.socketpair()
        self.client_sock = client
        self.conn = Connection(self._sock)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def commands(self):
        return [m["cmd"] for m in self.received]

    def push_voice(self, **kw):
        self.voice.update(kw)
        self.conn.send(OP_FRAME, {"cmd": "DISPATCH", "evt": "VOICE_SETTINGS_UPDATE",
                                  "data": dict(self.voice)})

    def hang_up(self):
        self.conn.send(OP_CLOSE, {"code": 1000, "message": "bye"})

    def _serve(self):
        try:
            op, hs = self.conn.recv()
            assert op == OP_HANDSHAKE and hs == {"v": 1, "client_id": CLIENT_ID}
            if self.ping_first:
                self.conn.send(OP_PING, {"n": 1})
                self.pong = self.conn.recv()
            self.conn.send(OP_FRAME, {"cmd": "DISPATCH", "evt": "READY",
                                      "data": {"v": 1, "user": {"username": "tonio"}}})
            while True:
                op, msg = self.conn.recv()
                self.received.append(msg)
                self._handle(msg)
        except (ConnectionError, OSError):
            pass

    def _reply(self, msg, data=None, error=None):
        out = {"cmd": msg["cmd"], "nonce": msg["nonce"], "data": data or {}}
        if error:
            out.update(evt="ERROR", data={"code": error[0], "message": error[1]})
        self.conn.send(OP_FRAME, out)

    def _handle(self, msg):
        cmd, args = msg["cmd"], msg["args"]
        if cmd == "AUTHENTICATE":
            if args["access_token"] in self.valid_tokens:
                self._reply(msg, {"user": {"username": "tonio"}})
            else:
                self._reply(msg, error=(4009, "Invalid OAuth2 access token"))
        elif cmd == "AUTHORIZE":
            assert args["client_id"] == CLIENT_ID and "rpc.voice.write" in args["scopes"]
            if self.authorize_code is None:
                self._reply(msg, error=(5000, "OAuth2 Error: access_denied"))
            else:
                self._reply(msg, {"code": self.authorize_code})
        elif cmd == "SUBSCRIBE":
            self._reply(msg, {"evt": msg["evt"]})
        elif cmd == "GET_VOICE_SETTINGS":
            self._reply(msg, dict(self.voice))
        elif cmd == "SET_VOICE_SETTINGS":
            self.voice.update({k: v for k, v in args.items() if k in ("mute", "deaf")})
            self._reply(msg, dict(self.voice))
            self.push_voice()


class TokenEndpoint:
    def __init__(self, responses):
        self.responses = dict(responses)  # grant_type -> token dict, or an exception
        self.requests = []

    def __call__(self, form):
        self.requests.append(dict(form))
        result = self.responses[form["grant_type"]]
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture(autouse=True)
def fast_retry(monkeypatch):
    monkeypatch.setattr(discord_module, "RETRY_S", 0.01)


@pytest.fixture
def paths(tmp_path):
    secret = tmp_path / "discord-secret"
    secret.write_text("s3cret\n")
    return secret, tmp_path / "discord-token.json"


def start(paths, servers, endpoint=None):
    """Run a client whose connect() hands out the fake servers in order."""
    pending = list(servers)

    def connect():
        if not pending:
            raise FileNotFoundError("Discord isn't running")
        return pending.pop(0).client_sock

    secret, token = paths
    client = Discord(CLIENT_ID, secret, token, connect=connect,
                     token_request=endpoint or TokenEndpoint({}))
    states = queue.Queue()
    client.watch(states.put)
    assert states.get(timeout=TIMEOUT) == DiscordState()  # initial "not connected"
    return client, states


def wait_for(states, expected):
    while True:
        s = states.get(timeout=TIMEOUT)
        if s == expected:
            return s


CONNECTED = DiscordState(True, False, False)


def test_first_run_authorizes_and_saves_token(paths):
    server = FakeDiscord(valid_tokens={"fresh"})
    endpoint = TokenEndpoint({"authorization_code": {"access_token": "fresh",
                                                     "refresh_token": "r1"}})
    client, states = start(paths, [server], endpoint)
    try:
        wait_for(states, CONNECTED)
    finally:
        client.stop()
    assert server.commands() == ["AUTHORIZE", "AUTHENTICATE", "SUBSCRIBE",
                                 "GET_VOICE_SETTINGS"]
    assert endpoint.requests == [{
        "grant_type": "authorization_code", "code": "the-code",
        "redirect_uri": "http://localhost", "client_id": CLIENT_ID, "client_secret": "s3cret"}]
    token_path = paths[1]
    assert json.loads(token_path.read_text())["access_token"] == "fresh"
    assert stat.S_IMODE(os.stat(token_path).st_mode) == 0o600


def test_saved_token_skips_the_popup(paths):
    paths[1].write_text(json.dumps({"access_token": "good", "refresh_token": "r"}))
    server = FakeDiscord()
    client, states = start(paths, [server])
    try:
        wait_for(states, CONNECTED)
    finally:
        client.stop()
    assert "AUTHORIZE" not in server.commands()


def test_expired_token_is_refreshed(paths):
    paths[1].write_text(json.dumps({"access_token": "old", "refresh_token": "r1"}))
    server = FakeDiscord(valid_tokens={"new"})
    endpoint = TokenEndpoint({"refresh_token": {"access_token": "new", "refresh_token": "r2"}})
    client, states = start(paths, [server], endpoint)
    try:
        wait_for(states, CONNECTED)
    finally:
        client.stop()
    assert "AUTHORIZE" not in server.commands()
    assert endpoint.requests[0]["refresh_token"] == "r1"
    assert json.loads(paths[1].read_text())["refresh_token"] == "r2"  # rotated token kept


def test_revoked_token_falls_back_to_the_popup(paths):
    paths[1].write_text(json.dumps({"access_token": "old", "refresh_token": "r1"}))
    server = FakeDiscord(valid_tokens={"fresh"})
    endpoint = TokenEndpoint({"refresh_token": OAuthError("HTTP 400: invalid_grant"),
                              "authorization_code": {"access_token": "fresh"}})
    client, states = start(paths, [server], endpoint)
    try:
        wait_for(states, CONNECTED)
    finally:
        client.stop()
    assert "AUTHORIZE" in server.commands()


def test_declined_popup_backs_off(paths, monkeypatch):
    monkeypatch.setattr(discord_module, "AUTH_RETRY_S", 60)
    server, second = FakeDiscord(authorize_code=None), FakeDiscord()
    endpoint = TokenEndpoint({})
    client, states = start(paths, [server, second], endpoint)
    try:
        server.thread.join(TIMEOUT)  # client hangs up after the refusal
        assert not server.thread.is_alive()
        assert second.received == []  # not reconnected (and re-prompted) straight away
    finally:
        client.stop()
    assert endpoint.requests == []
    assert states.empty()  # never connected


def test_voice_changes_in_discord_reach_the_callback(paths):
    paths[1].write_text(json.dumps({"access_token": "good"}))
    server = FakeDiscord()
    client, states = start(paths, [server])
    try:
        wait_for(states, CONNECTED)
        server.push_voice(mute=True)
        assert states.get(timeout=TIMEOUT) == DiscordState(True, True, False)
        server.push_voice(deaf=True)
        assert states.get(timeout=TIMEOUT) == DiscordState(True, True, True)
    finally:
        client.stop()


def test_toggles_send_the_opposite_of_current_state(paths):
    paths[1].write_text(json.dumps({"access_token": "good"}))
    server = FakeDiscord(mute=False, deaf=False)
    client, states = start(paths, [server])
    try:
        wait_for(states, CONNECTED)
        client.toggle_mute()
        wait_for(states, DiscordState(True, True, False))
        client.toggle_deafen()
        wait_for(states, DiscordState(True, True, True))
        client.toggle_mute()  # deafened counts as muted -> un-mute
        wait_for(states, DiscordState(True, False, True))
    finally:
        client.stop()
    sets = [m["args"] for m in server.received if m["cmd"] == "SET_VOICE_SETTINGS"]
    assert sets == [{"mute": True}, {"deaf": True}, {"mute": False}]


def test_toggle_while_disconnected_is_ignored(paths):
    client, states = start(paths, [])
    client.toggle_mute()  # must not raise
    client.stop()


def test_reconnects_after_discord_quits(paths):
    paths[1].write_text(json.dumps({"access_token": "good"}))
    first, second = FakeDiscord(), FakeDiscord(mute=True)
    client, states = start(paths, [first, second])
    try:
        wait_for(states, CONNECTED)
        first.hang_up()
        assert states.get(timeout=TIMEOUT) == DiscordState()
        wait_for(states, DiscordState(True, True, False))
    finally:
        client.stop()


def test_answers_pings(paths):
    paths[1].write_text(json.dumps({"access_token": "good"}))
    server = FakeDiscord(ping_first=True)
    client, states = start(paths, [server])
    try:
        wait_for(states, CONNECTED)
    finally:
        client.stop()
    assert server.pong == (OP_PONG, {"n": 1})


def test_missing_secret_means_no_connection(paths):
    paths[0].unlink()
    server = FakeDiscord()
    client, states = start(paths, [server])
    threading.Event().wait(0.05)
    client.stop()
    assert server.received == [] and states.empty()


def test_socket_candidates_include_flatpak():
    paths = socket_candidates({"XDG_RUNTIME_DIR": "/run/user/1000"})
    assert paths[0].as_posix() == "/run/user/1000/discord-ipc-0"
    assert any(p.as_posix() == "/run/user/1000/app/com.discordapp.Discord/discord-ipc-0"
               for p in paths)


def test_urllib_not_imported_until_needed():
    """urllib.request drags in ssl and friends (~5 MB); only token requests need it."""
    import subprocess
    import sys
    code = ("import sys, launchpad_control.app; "
            "print(sorted(m for m in ('urllib.request', 'ssl') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True).stdout
    assert out.strip() == "[]"
