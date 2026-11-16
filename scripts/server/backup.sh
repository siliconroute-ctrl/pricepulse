#!/usr/bin/env bash
# Runs ON THE SERVER, from ~/pricepulse, as the deploy user (it asks for the sudo password once).
# Makes a safe copy of the database in ~/pricepulse/pricepulse-backup-DATE.db.
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT="pricepulse-backup-$(date +%F).db"
sudo docker compose exec -T api rm -f /data/backup.db
sudo docker compose exec -T api python -c "import sqlite3; s=sqlite3.connect('/data/pricepulse.db'); s.execute(\"VACUUM INTO '/data/backup.db'\")"
sudo docker compose cp api:/data/backup.db "./$OUT"
sudo docker compose exec -T api rm -f /data/backup.db
sudo chown "$(id -un)":"$(id -gn)" "./$OUT"
echo "Backup made: $OUT ($(du -h "$OUT" | cut -f1))"
