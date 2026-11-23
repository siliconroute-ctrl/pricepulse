#!/usr/bin/env bash
# Looks for the items paid shoppers asked for (new-item searches). Started every 5 minutes by a
# systemd user timer (see docs/AUTOMATION.md). Does nothing, and writes no log, when nothing waits.
# Never needs sudo and never asks for a password.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOGDIR="$HOME/pricepulse-logs"
mkdir -p "$LOGDIR"
# shellcheck disable=SC1090
[ -f "$HOME/.config/pricepulse/env" ] && . "$HOME/.config/pricepulse/env"
cd "$REPO/backend" || exit 1
out="$("$REPO/backend/.venv/bin/python" search_worker.py 2>&1)"
code=$?
if [ -n "$out" ]; then
  printf '--- %s ---\n%s\n' "$(date '+%F %T')" "$out" >>"$LOGDIR/ondemand-$(date +%Y%m).log"
fi
find "$LOGDIR" -name 'ondemand-*.log' -mtime +90 -delete 2>/dev/null
exit "$code"
