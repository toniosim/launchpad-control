#!/usr/bin/env python3
"""Claude Code hook: forward the hook event to launchpad-control. Stdlib only.

Claude Code runs this for each configured hook with the event's JSON on stdin. It sends
the few fields the daemon needs as one datagram to its Unix socket and exits at once.
It never prints (UserPromptSubmit output would be added to Claude's context) and always
exits 0, so a stopped daemon can't disturb Claude Code.

Install: scripts/install-claude-hooks.py
"""
import json
import os
import socket
import sys

FIELDS = ("hook_event_name", "session_id", "cwd", "tool_name", "notification_type")


def main() -> None:
    try:
        data = json.load(sys.stdin)
        msg = {k: data[k] for k in FIELDS if isinstance(data.get(k), str)}
        if isinstance(data.get("message"), str):
            msg["message"] = data["message"][:200]
        base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.setblocking(False)  # if the daemon is wedged, drop the event rather than wait
            s.sendto(json.dumps(msg).encode(), os.path.join(base, "launchpad-control",
                                                            "claude.sock"))
    except Exception:
        pass  # daemon not running, bad input, ...: never Claude Code's problem


if __name__ == "__main__":
    main()
