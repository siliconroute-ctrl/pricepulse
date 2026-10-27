"""Run the scrapers on a timetable: every PP_SCRAPE_EVERY_HOURS hours (default 6).

Stores run one at a time in random order, with a random pause between them, so no
site sees a burst. Each store runs in its own process, so one crash cannot stop
the others or the scheduler.

    python scheduler.py            # run forever
    python scheduler.py --once     # one round, then stop
"""

from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys
import time
from pathlib import Path

from pricepulse.ingest import sa_now
from pricepulse.scrapers.stores import ALL_SCRAPERS

HERE = Path(__file__).resolve().parent


def one_round(regions: list[str], gap: tuple[float, float]) -> None:
    stores = [c.name for c in ALL_SCRAPERS]
    random.shuffle(stores)
    print(f"[{sa_now():%d %b %H:%M}] Starting a price check: {', '.join(stores)}", flush=True)
    for i, store in enumerate(stores):
        for region in regions:
            code = subprocess.call(
                [sys.executable, str(HERE / "run_scrapers.py"), "--region", region, "--store", store],
                cwd=HERE,
            )
            if code:
                print(
                    f"[{sa_now():%H:%M}] {store} ({region}) finished with problems (code {code}). "
                    "Moving on to the next store.",
                    flush=True,
                )
        if i < len(stores) - 1:
            time.sleep(random.uniform(*gap))
    print(f"[{sa_now():%d %b %H:%M}] Price check finished.", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Scrape on a timetable.")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    every = float(os.environ.get("PP_SCRAPE_EVERY_HOURS", "6"))
    regions = [r.strip() for r in os.environ.get("PP_SCRAPE_REGIONS", "paarl").split(",") if r.strip()]
    gap = (60.0, 300.0)
    # Start a little after boot, at a random moment, so restarts don't all hit at once.
    time.sleep(random.uniform(5, 60) if not args.once else 0)
    while True:
        started = time.monotonic()
        one_round(regions, gap)
        if args.once:
            return 0
        wait = max(600.0, every * 3600 - (time.monotonic() - started)) + random.uniform(0, 900)
        print(f"Next price check in about {wait / 3600:.1f} hours.", flush=True)
        time.sleep(wait)


if __name__ == "__main__":
    sys.exit(main())
