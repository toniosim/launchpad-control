#!/usr/bin/env bash
# Install (and by default enable + start) the launchpad-control systemd user service.
#   scripts/install-service.sh              install, enable at login, start now
#   scripts/install-service.sh --unit-only  only refresh the unit file (used by update.sh)
#   scripts/install-service.sh --uninstall  stop, disable and remove it
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT=launchpad-control.service
DEST="$HOME/.config/systemd/user/$UNIT"

case "${1:-}" in
  --uninstall)
    systemctl --user disable --now "$UNIT" 2>/dev/null || true
    rm -f "$DEST" "$HOME/.local/bin/lpctl"
    systemctl --user daemon-reload
    echo "Removed $UNIT"
    exit 0 ;;
  ""|--unit-only) ;;
  *) echo "usage: $0 [--unit-only|--uninstall]" >&2; exit 2 ;;
esac

if [ ! -x "$REPO/.venv/bin/launchpad-control" ]; then
  echo "Setting up the venv in $REPO/.venv"
  python3 -m venv "$REPO/.venv"
  "$REPO/.venv/bin/pip" install -e "$REPO[dev]"
fi

mkdir -p "$(dirname "$DEST")"
rendered="$(sed "s|@REPO@|$REPO|g" "$REPO/systemd/$UNIT")"
if [ ! -f "$DEST" ] || [ "$rendered" != "$(cat "$DEST")" ]; then
  printf '%s\n' "$rendered" > "$DEST"
  systemctl --user daemon-reload
  echo "Installed $DEST"
fi

[ "${1:-}" = "--unit-only" ] && exit 0

mkdir -p "$HOME/.local/bin"
ln -sf "$REPO/scripts/lpctl" "$HOME/.local/bin/lpctl"

# Two instances would both act on every press (e.g. double-toggle mic mute), so if one
# is already running outside the service, enable for next login but don't start.
main_pid="$(systemctl --user show -p MainPID --value "$UNIT")"
others="$(pgrep -f 'bin/launchpad-control( |$)' | grep -vx "${main_pid:-0}" || true)"
if [ -n "$others" ]; then
  systemctl --user enable "$UNIT"
  echo "Enabled at login, but NOT started: launchpad-control is already running (PID $(echo $others))."
  echo "Stop it (Ctrl+C in its terminal), then run: lpctl start"
else
  systemctl --user enable --now "$UNIT"
  echo "Enabled at login and started."
fi
echo "Manage it with: lpctl {status|stop|start|restart|logs|update|dev}"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "Note: $HOME/.local/bin is not on your PATH; use $REPO/scripts/lpctl" ;; esac
