#!/usr/bin/env bash
# Apply local changes to the running service: refresh deps if needed, test, restart.
# Does nothing risky: if the tests or config check fail, the running service is left alone.
# If you stopped the service on purpose it stays stopped.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT=launchpad-control.service
VENV="$REPO/.venv"
STAMP="$VENV/.pyproject.sha"

cd "$REPO"
[ -x "$VENV/bin/launchpad-control" ] || { echo "No venv; run scripts/install-service.sh first" >&2; exit 1; }

sha="$(sha256sum pyproject.toml | cut -d' ' -f1)"
if [ "$(cat "$STAMP" 2>/dev/null || true)" != "$sha" ]; then
  echo "pyproject.toml changed: updating dependencies"
  "$VENV/bin/pip" install -q -e '.[dev]'
  echo "$sha" > "$STAMP"
fi

echo "Running tests"
"$VENV/bin/pytest" -q
"$VENV/bin/launchpad-control" --check-config

"$REPO/scripts/install-service.sh" --unit-only

if systemctl --user is-active --quiet "$UNIT"; then
  systemctl --user restart "$UNIT"
  echo "Restarted $UNIT"
else
  echo "$UNIT is not running; leaving it stopped (lpctl start to run it)"
fi
