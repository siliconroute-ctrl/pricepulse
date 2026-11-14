"""Check the price files before they are sent to the server. Used by scripts/upload_prices.ps1.

    python upload_check.py                 # check data/latest_{town}.json for every town
    python upload_check.py --max-hours 36

Prints what each file holds, then one "FILE <path>" line per file that is good to send.
Exit code 0 = send them; 1 = do not send (the reason is printed in plain words).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

from pricepulse.config import get_settings
from pricepulse.ingest import parse_scraped_at, utcnow
from pricepulse.regions import load_regions
from pricepulse.scrapers.stores import ACTIVE_SCRAPERS


def check_file(
    path: Path, max_hours: float, expected: list[str]
) -> tuple[list[str], list[str], dict[str, int]]:
    """Return (problems, warnings, prices per store) for one price file."""
    problems: list[str] = []
    warnings: list[str] = []
    if not path.exists():
        return [f"{path.name} does not exist. Run run_scrapers.py first."], warnings, {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{path.name} cannot be read ({exc})."], warnings, {}
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        return [f"{path.name} holds no prices."], warnings, {}
    if data.get("sample"):
        problems.append(f"{path.name} is marked as EXAMPLE (made-up) data.")
    stamp = data.get("scraped_at")
    if not stamp:
        problems.append(f"{path.name} has no date, so its age cannot be checked.")
    elif utcnow() - parse_scraped_at(stamp) > timedelta(hours=max_hours):
        problems.append(
            f"{path.name} is older than {max_hours:g} hours (scraped {stamp}). Run the scrapers again."
        )
    per_store: dict[str, int] = {}
    for row in items:
        per_store[str(row.get("store", "?"))] = per_store.get(str(row.get("store", "?")), 0) + 1
    for store in expected:
        if store not in per_store:
            warnings.append(f"{path.name} has no prices from {store}.")
    return problems, warnings, per_store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check price files before upload")
    ap.add_argument("--max-hours", type=float, default=36.0)
    args = ap.parse_args(argv)
    settings = get_settings()
    regions = load_regions(str(settings.regions_file))
    expected = [c.name for c in ACTIVE_SCRAPERS]
    good: list[Path] = []
    bad = 0
    for region in regions.regions:
        path = settings.data_dir / f"latest_{region}.json"
        problems, warnings, per_store = check_file(path, args.max_hours, expected)
        name = regions.regions[region].name
        if per_store:
            print(
                f"{name}: {sum(per_store.values())} prices - "
                + ", ".join(f"{k} {v}" for k, v in sorted(per_store.items()))
            )
        for text in warnings:
            print(f"  Note: {text}")
        for text in problems:
            print(f"  PROBLEM: {text}")
        if problems:
            bad += 1
        else:
            good.append(path)
    if bad:
        print(f"\nNothing will be sent: {bad} of {len(regions.regions)} town files have a problem.")
        return 1
    print()
    for path in good:
        print(f"FILE {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
