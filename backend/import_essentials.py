"""Import essential items from CSV files saved in a folder (the synced Google Drive folder).

    python import_essentials.py                 # every *.csv in ~/pricepulse-imports/inbox
    python import_essentials.py --dir /some/folder

Each file needs a column called item (or Item Name, name, term, product; or just the first
column) and may have a column called category (Groceries, Fresh, Health & Beauty, Liquor).
Commas, semicolons or tabs, quotes and a leading BOM all work. A file that was already imported
and has not changed is skipped, so running this every night is safe. The server checks every
row again and refuses bad ones. The upload token is read the same way as push_prices.py and is
never printed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from push_prices import DEFAULT_URL, read_token

MAX_ROWS = 500
HEADS = ["item", "item name", "name", "term", "product", "description"]
CATS = ["category", "type", "department"]


def parse_rows(text: str) -> list[dict]:
    """CSV text to [{"term", "category"}], same reading rules as the admin screen."""
    text = text.lstrip("﻿")
    first = text.splitlines()[0] if text.strip() else ""
    sep = ","
    for c in (";", "\t"):
        if first.count(c) > first.count(sep):
            sep = c
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=sep) if any(x.strip() for x in r)]
    if not rows:
        return []
    head = [x.strip().lower() for x in rows[0]]
    ic = next((i for i, h in enumerate(head) if h in HEADS), -1)
    cc = next((i for i, h in enumerate(head) if h in CATS), -1)
    has_head = ic >= 0
    if not has_head:
        ic, cc = 0, (1 if len(rows[0]) > 1 else -1)
    out = []
    for r in rows[1 if has_head else 0 :]:
        term = (r[ic] if ic < len(r) else "").strip()
        if term:
            out.append({"term": term, "category": (r[cc] if 0 <= cc < len(r) else "").strip()})
    return out


def post(url: str, token: str, rows: list[dict], timeout: float = 60.0) -> dict:
    req = urllib.request.Request(
        url.rstrip("/") + "/api/v1/ingest/terms",
        data=json.dumps({"rows": rows}).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "PricePulse-scraper/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def import_folder(folder: Path, url: str, token: str, state_file: Path, send=post) -> tuple[int, list[str]]:
    """Import new or changed files. Returns (failures, lines to print)."""
    lines: list[str] = []
    try:
        done = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        done = {}
    failures = 0
    files = sorted(p for p in folder.glob("*") if p.suffix.lower() in (".csv", ".txt") and p.is_file())
    if not files:
        return 0, [f"No CSV files in {folder}."]
    for path in files:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if done.get(path.name) == digest:
            lines.append(f"{path.name}: already imported, unchanged.")
            continue
        try:
            rows = parse_rows(raw.decode("utf-8-sig", "replace"))
            if not rows:
                lines.append(
                    f"{path.name}: no items found (needs an 'item' column or items in the first column)."
                )
                failures += 1
                continue
            added = existing = invalid = 0
            reasons: list[str] = []
            for start in range(0, len(rows), MAX_ROWS):
                answer = send(url, token, rows[start : start + MAX_ROWS])
                added += answer["added"]
                existing += answer["existing"]
                invalid += answer["invalid_count"]
                reasons += [
                    f"row {x['row']}: {x['term']} ({x['why']})" for x in answer.get("invalid", [])[:3]
                ]
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
            lines.append(f"{path.name}: NOT imported ({exc}). It will be tried again next time.")
            failures += 1
            continue
        done[path.name] = digest
        note = f"{path.name}: {added} added, {existing} already there"
        note += f", {invalid} skipped" if invalid else ""
        lines.append(note + ".")
        lines += [f"    skipped {r}" for r in reasons[:5]]
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(done, indent=1), encoding="utf-8")
    return failures, lines


def main(argv: list[str] | None = None) -> int:
    default_dir = os.environ.get("PP_IMPORT_DIR", str(Path.home() / "pricepulse-imports" / "inbox"))
    ap = argparse.ArgumentParser(description="Import essential items from CSV files in a folder")
    ap.add_argument("--dir", default=default_dir)
    ap.add_argument("--url", default=os.environ.get("PP_UPLOAD_URL", DEFAULT_URL))
    args = ap.parse_args(argv)
    token = read_token()
    if not token:
        print("No upload token (see docs/AUTOMATION.md), so nothing was imported.")
        return 1
    folder = Path(args.dir).expanduser()
    if not folder.is_dir():
        print(f"The folder {folder} does not exist yet, so nothing was imported.")
        return 0
    failures, lines = import_folder(folder, args.url, token, folder.parent / "imported.json")
    print("\n".join(lines))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
