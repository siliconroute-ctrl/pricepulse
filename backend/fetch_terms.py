"""Fetch the essential search words from the server before the night scrape.

    python fetch_terms.py

Writes ``server_terms.txt`` in the data folder, in the same format as products.txt, and
``run_scrapers.py`` adds those words to the ones in products.txt. Uses the same upload token
as push_prices.py. If the server cannot be reached the old file (if any) is kept, so the night
run never fails because of this. The token is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from pricepulse.config import get_settings
from push_prices import DEFAULT_URL, read_token


def to_text(terms: list[dict]) -> str:
    """products.txt format: a '# category: X' line, then that category's words."""
    by: dict[str, list[str]] = {}
    for t in terms:
        term = str(t.get("term", "")).strip()
        if term:
            by.setdefault(str(t.get("category") or "Groceries"), []).append(term)
    lines = ["# From the server's essential items list (admin area). Written by fetch_terms.py."]
    for cat in sorted(by):
        lines += ["", f"# category: {cat}"] + by[cat]
    return "\n".join(lines) + "\n"


def fetch(url: str, token: str, timeout: float = 30.0) -> list[dict]:
    req = urllib.request.Request(
        url.rstrip("/") + "/api/v1/ingest/terms",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "PricePulse-scraper/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8")).get("terms", [])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch the essential items list from the server")
    ap.add_argument("--url", default=os.environ.get("PP_UPLOAD_URL", DEFAULT_URL))
    args = ap.parse_args(argv)
    token = read_token()
    if not token:
        print("No upload token, so the server's item list was not fetched.")
        return 1
    out = get_settings().data_dir / "server_terms.txt"
    try:
        terms = fetch(args.url, token)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"Could not fetch the server's item list ({exc}). Using the last copy, if there is one.")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_text(terms), encoding="utf-8")
    print(f"Server item list: {len(terms)} items saved to {out.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
