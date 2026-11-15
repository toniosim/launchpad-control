"""Claude Code session status, fed by Claude Code hooks.

Each hook runs `scripts/claude_hook.py`, which forwards a few fields of the hook's JSON
as one datagram to a Unix socket in $XDG_RUNTIME_DIR. `ClaudeListener` receives them
(event driven: one thread blocked in recv, no polling) and `Sessions` turns them into a
status per session: ready, working, or needs input (Claude Code's own term for "your
turn": a permission prompt, a question, or a finished turn waiting for your reply).
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

READY, WORKING, NEEDS_INPUT = "ready", "working", "needs_input"

# Tools that stop and wait for the user without a permission prompt.
ASKING_TOOLS = {"AskUserQuestion", "ExitPlanMode"}
# Notifications that mean "answer me"; idle_prompt (waiting 60s after finishing) and
# auth_success don't change anything.
ASKING_NOTIFICATIONS = {"permission_prompt", "elicitation_dialog"}

MAX_DATAGRAM = 64 * 1024


def socket_path() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(base) / "launchpad-control" / "claude.sock"


@dataclass(frozen=True)
class HookEvent:
    """The parts of one Claude Code hook call that matter here."""
    event: str          # hook_event_name, e.g. "UserPromptSubmit"
    session_id: str
    cwd: str = ""
    tool: str = ""      # tool_name (PreToolUse/PostToolUse)
    notification: str = ""  # notification_type (Notification)
    message: str = ""   # Notification message, used when notification_type is missing


def parse_hook(data: bytes) -> HookEvent | None:
    try:
        d = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(d, dict):
        return None
    event, sid = d.get("hook_event_name"), d.get("session_id")
    if not (isinstance(event, str) and isinstance(sid, str) and event and sid):
        return None

    def s(key: str) -> str:
        v = d.get(key)
        return v if isinstance(v, str) else ""
    return HookEvent(event, sid, s("cwd"), s("tool_name"), s("notification_type"),
                     s("message"))


def status_after(event: HookEvent, current: str | None) -> str | None:
    """New status for a session after `event`; None = the session ended.

    `current` is the session's status so far, or None if it's new.
    """
    e = event.event
    if e == "SessionEnd":
        return None
    if e == "SessionStart":
        # Also sent on /clear, resume and auto-compact (mid-task): keep what we know.
        return current or READY
    if e == "UserPromptSubmit":
        return WORKING
    if e == "PreToolUse":
        return NEEDS_INPUT if event.tool in ASKING_TOOLS else WORKING
    if e == "PostToolUse":
        return WORKING  # a tool ran, so any permission prompt was answered
    if e == "PermissionRequest":
        return NEEDS_INPUT
    if e == "Notification":
        kind = event.notification
        if kind in ASKING_NOTIFICATIONS or (not kind and "permission" in event.message.lower()):
            return NEEDS_INPUT
        return current or READY
    if e == "Stop":
        return NEEDS_INPUT  # finished its turn: waiting for your reply
    return current or READY  # SubagentStop, PreCompact, anything newer: no change


@dataclass(frozen=True)
class Session:
    id: str
    cwd: str
    status: str
    last_event: float  # clock time of the last hook from it


@dataclass(frozen=True)
class Sessions:
    """Known sessions, oldest first. Immutable: every change returns a new one."""
    items: tuple[Session, ...] = field(default_factory=tuple)

    def apply(self, event: HookEvent, now: float) -> "Sessions":
        old = self.get(event.session_id)
        status = status_after(event, old.status if old else None)
        if status is None:
            return Sessions(tuple(s for s in self.items if s.id != event.session_id))
        if old is None:
            return Sessions(self.items + (Session(event.session_id, event.cwd, status, now),))
        new = replace(old, status=status, last_event=now, cwd=event.cwd or old.cwd)
        return Sessions(tuple(new if s.id == old.id else s for s in self.items))

    def get(self, session_id: str) -> Session | None:
        return next((s for s in self.items if s.id == session_id), None)

    def dismiss(self, session_id: str) -> "Sessions":
        return Sessions(tuple(s for s in self.items if s.id != session_id))

    def prune(self, now: float, stale_after: float) -> "Sessions":
        """Forget sessions silent for `stale_after` seconds (killed without a SessionEnd)."""
        keep = tuple(s for s in self.items if now - s.last_event < stale_after)
        return self if len(keep) == len(self.items) else Sessions(keep)

    def any(self, status: str) -> bool:
        return any(s.status == status for s in self.items)


class ClaudeListener:
    """Receives hook datagrams on a Unix socket and passes them on as HookEvents."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or socket_path()
        self._sock: socket.socket | None = None
        self._stopping = False

    def watch(self, callback: Callable[[HookEvent], None]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.path.unlink()  # left over from a crash; a second instance would steal it
        except FileNotFoundError:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self._sock = sock
        threading.Thread(target=self._loop, args=(sock, callback), name="claude-hooks",
                         daemon=True).start()
        log.debug("Listening for Claude Code hooks on %s", self.path)

    def stop(self) -> None:
        if self._sock is None:
            return
        self._stopping = True
        try:  # wake the blocked recv; closing alone doesn't
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
                s.sendto(b"", str(self.path))
        except OSError:
            pass
        try:
            self.path.unlink()
        except OSError:
            pass

    def _loop(self, sock: socket.socket, callback: Callable[[HookEvent], None]) -> None:
        with sock:
            while not self._stopping:
                try:
                    data = sock.recv(MAX_DATAGRAM)
                except OSError:
                    return
                event = parse_hook(data) if data else None
                if event is not None:
                    callback(event)
                elif data:
                    log.debug("Ignoring malformed Claude hook datagram")
