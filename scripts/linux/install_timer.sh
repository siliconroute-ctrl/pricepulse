#!/usr/bin/env bash
# One-time setup of the nightly timer for YOUR user (no root needed for this part).
# Afterwards run once:  sudo loginctl enable-linger "$USER"   so it runs even when nobody is logged in.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [ "$REPO" != "$HOME/pricepulse" ]; then
  echo "The repository must be at $HOME/pricepulse (it is at $REPO)." >&2
  exit 1
fi
mkdir -p "$HOME/.config/systemd/user"
cp "$REPO/scripts/linux/pricepulse-nightly.service" "$REPO/scripts/linux/pricepulse-nightly.timer" \
   "$REPO/scripts/linux/pricepulse-ondemand.service" "$REPO/scripts/linux/pricepulse-ondemand.timer" \
   "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now pricepulse-nightly.timer
systemctl --user enable --now pricepulse-ondemand.timer
echo "Installed. Next run:"
systemctl --user list-timers pricepulse-nightly.timer pricepulse-ondemand.timer --no-pager
