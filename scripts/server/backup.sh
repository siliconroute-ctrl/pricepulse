#!/usr/bin/env bash
# Runs ON THE SERVER, from ~/pricepulse, as the deploy user (it asks for the sudo password once).
# Makes a safe copy of the database in ~/pricepulse/pricepulse-backup-DATE.db, and (when there are
# any) of the shop photos in ~/pricepulse/pricepulse-uploads-DATE.tar.gz.
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT="pricepulse-backup-$(date +%F).db"
sudo docker compose exec -T api rm -f /data/backup.db
sudo docker compose exec -T api python -c "import sqlite3; s=sqlite3.connect('/data/pricepulse.db'); s.execute(\"VACUUM INTO '/data/backup.db'\")"
sudo docker compose cp api:/data/backup.db "./$OUT"
sudo docker compose exec -T api rm -f /data/backup.db
sudo chown "$(id -un)":"$(id -gn)" "./$OUT"
echo "Backup made: $OUT ($(du -h "$OUT" | cut -f1))"
# Shop photos uploaded by store owners live in /data/uploads (they are not in the database).
if sudo docker compose exec -T api test -d /data/uploads; then
  PICS="pricepulse-uploads-$(date +%F).tar.gz"
  sudo docker compose exec -T api tar -czf /data/uploads-backup.tar.gz -C /data uploads
  sudo docker compose cp api:/data/uploads-backup.tar.gz "./$PICS"
  sudo docker compose exec -T api rm -f /data/uploads-backup.tar.gz
  sudo chown "$(id -un)":"$(id -gn)" "./$PICS"
  echo "Shop photos backed up: $PICS ($(du -h "$PICS" | cut -f1))"
fi
