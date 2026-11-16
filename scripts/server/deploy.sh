#!/usr/bin/env bash
# Runs ON THE SERVER, from ~/pricepulse, as the deploy user (it asks for the sudo password once).
# Brings the code up to date, rebuilds the app and checks that it answers.
set -euo pipefail
cd "$(dirname "$0")/../.."
git pull --ff-only origin main
sudo docker compose up -d --build --force-recreate api
for i in $(seq 1 30); do
  if curl -fsS -m 5 http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1; then
    echo "The app is answering again."
    exit 0
  fi
  sleep 2
done
echo "The app did not answer within 60 seconds. Run: sudo docker compose logs --tail 50 api"
exit 1
