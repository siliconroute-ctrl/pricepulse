#!/usr/bin/env bash
# Runs ON THE SERVER (started by scripts/upload_prices.ps1): loads the uploaded price files
# into the app, then deletes them. Usage: bash scripts/load_prices.sh [yes]
#   yes = also remove the example (made-up) prices once the real ones are in.
# Without "yes" it only shows how many example prices WOULD be removed.
set -euo pipefail
REMOVE="${1:-no}"
cd "$HOME/pricepulse"
shopt -s nullglob
files=("$HOME"/pricepulse-upload/latest_*.json)
if [ "${#files[@]}" -eq 0 ]; then
  echo "No uploaded price files found in ~/pricepulse-upload. Nothing was changed."
  exit 1
fi
echo "Loading ${#files[@]} price files..."
sudo docker compose exec -T api mkdir -p /tmp/up
for f in "${files[@]}"; do
  sudo docker compose cp "$f" "api:/tmp/up/$(basename "$f")"
done
sudo docker compose exec -T api sh -c 'python admin.py ingest /tmp/up/latest_*.json'
if [ "$REMOVE" = "yes" ]; then
  sudo docker compose exec -T api python admin.py purge-sample --yes
else
  sudo docker compose exec -T api python admin.py purge-sample
  echo "(Example prices were NOT removed. Add -RemoveExamples to the upload command to remove them.)"
fi
sudo docker compose exec -T api rm -rf /tmp/up
rm -f "${files[@]}"
echo "Done. The uploaded files were deleted from the server."
