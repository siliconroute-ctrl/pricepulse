#!/usr/bin/env bash
# The hands-off night run on the Linux scraper machine: scrape every store, then upload.
# Started by the systemd timer (see docs/AUTOMATION.md). Safe to start by hand too.
# Never needs sudo and never asks for a password. Logs to ~/pricepulse-logs/.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOGDIR="$HOME/pricepulse-logs"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/nightly-$(date +%Y%m%d-%H%M).log"
exec >>"$LOG" 2>&1

# One run at a time: if the last night's run is somehow still going, stop quietly.
exec 9>"$LOGDIR/.nightly.lock"
if ! flock -n 9; then
  echo "A previous run is still going. Not starting another."
  exit 0
fi

# Optional settings (alert mailbox etc.), readable only by you. Never put them in the repository.
# shellcheck disable=SC1090
[ -f "$HOME/.config/pricepulse/env" ] && . "$HOME/.config/pricepulse/env"

cd "$REPO/backend" || exit 1
PY="$REPO/backend/.venv/bin/python"
echo "=== PricePulse night run started $(date '+%F %T') ==="

# Pick up fixes and new product words (a pull that fails is not fatal: the old code still runs).
git -C "$REPO" pull --ff-only || echo "(git pull did not work; carrying on with the code we have)"

# Essential items dropped into the linked Google Drive folder (only when rclone is set up, see
# docs/AUTOMATION.md). Never fatal: the night run goes on whatever happens here.
if [ -n "${PP_DRIVE_REMOTE:-}" ] && command -v rclone >/dev/null 2>&1; then
  INBOX="${PP_IMPORT_DIR:-$HOME/pricepulse-imports/inbox}"
  mkdir -p "$INBOX"
  rclone copy "$PP_DRIVE_REMOTE" "$INBOX" --include "*.csv" --include "*.CSV" --include "*.txt" \
    || echo "(could not read the Drive folder; will try again tomorrow)"
  "$PY" import_essentials.py || echo "(essential items import had a problem; see above)"
fi

# Essential items added in the admin area (not fatal if the server cannot be reached).
"$PY" fetch_terms.py || echo "(could not fetch the server's item list; using the last copy and products.txt)"

"$PY" run_scrapers.py --no-db
echo "--- scrape finished with code $? at $(date '+%T') ---"

"$PY" push_prices.py
code=$?
echo "=== upload finished with code $code at $(date '+%F %T') ==="

# Keep 30 days of logs.
find "$LOGDIR" -name 'nightly-*.log' -mtime +30 -delete 2>/dev/null
exit "$code"
