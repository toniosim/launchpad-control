#!/usr/bin/env python3
"""Add (or with --uninstall, remove) the launchpad-control hooks in Claude Code's settings.

Edits ~/.claude/settings.json (or the file given with --settings), keeping everything
else as it is and saving a .bak copy first. Running it again replaces our hooks instead of
adding duplicates. Claude Code picks up the change in new sessions (or via /hooks).
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

HOOK_SCRIPT = Path(__file__).resolve().parent / "claude_hook.py"
COMMAND = f"/usr/bin/python3 -I -S {HOOK_SCRIPT}"
MARKER = "claude_hook.py"  # identifies our entries when updating or removing

# event -> matcher (None = the event takes no matcher; "" = every tool)
EVENTS = {
    "SessionStart": None,
    "UserPromptSubmit": None,
    "PreToolUse": "AskUserQuestion|ExitPlanMode",  # questions that wait for you
    "PostToolUse": "",
    "PermissionRequest": "",
    "Notification": "",
    "Stop": None,
    "SessionEnd": None,
}


def ours(entry: dict) -> bool:
    return any(MARKER in h.get("command", "") for h in entry.get("hooks", []))


def update(settings: dict, install: bool) -> dict:
    hooks = settings.setdefault("hooks", {})
    for event in list(hooks):
        hooks[event] = [e for e in hooks[event] if not ours(e)]
        if not hooks[event]:
            del hooks[event]
    if install:
        for event, matcher in EVENTS.items():
            entry: dict = {"hooks": [{"type": "command", "command": COMMAND, "timeout": 5}]}
            if matcher is not None:
                entry = {"matcher": matcher, **entry}
            hooks.setdefault(event, []).append(entry)
    if not hooks:
        del settings["hooks"]
    return settings


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--uninstall", action="store_true", help="remove our hooks")
    p.add_argument("--settings", type=Path, default=Path.home() / ".claude" / "settings.json")
    args = p.parse_args()

    path: Path = args.settings
    settings = json.loads(path.read_text()) if path.exists() else {}
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(update(settings, not args.uninstall), indent=2) + "\n")
    print(f"{'Removed' if args.uninstall else 'Installed'} launchpad-control hooks in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
