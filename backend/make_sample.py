"""Make EXAMPLE price data for testing the app without scraping.

These are made-up test figures, not shelf prices. Every file and database row this
writes is flagged as sample, and the app shows an "Example prices" banner for them.

    python make_sample.py                     # data/latest_paarl.json + database
    python make_sample.py --regions paarl,stellenbosch --days 14
    python make_sample.py --no-db
"""

from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta

from pricepulse.config import get_settings
from pricepulse.ingest import sa_now
from run_scrapers import write_atomic

# name, size, category, {store: example price}
SAMPLE = [
    (
        "Albany Superior White Bread",
        "700g",
        "Groceries",
        {"Checkers": 19.99, "Pick n Pay": 20.99, "Woolworths": 24.99},
    ),
    (
        "White Star Super Maize Meal",
        "5kg",
        "Groceries",
        {"Checkers": 64.99, "Pick n Pay": 66.99, "Woolworths": 72.99},
    ),
    ("Sasko Cake Flour", "2.5kg", "Groceries", {"Checkers": 39.99, "Pick n Pay": 41.99}),
    (
        "Tastic Long Grain Rice",
        "2kg",
        "Groceries",
        {"Checkers": 49.99, "Pick n Pay": 52.99, "Woolworths": 59.99},
    ),
    (
        "Koo Baked Beans in Tomato Sauce",
        "410g",
        "Groceries",
        {"Checkers": 14.99, "Pick n Pay": 15.99, "Woolworths": 17.99},
    ),
    (
        "Sunfoil Sunflower Oil",
        "2L",
        "Groceries",
        {"Checkers": 84.99, "Pick n Pay": 87.99, "Woolworths": 94.99},
    ),
    (
        "Ricoffy Instant Coffee",
        "250g",
        "Groceries",
        {"Checkers": 69.99, "Pick n Pay": 72.99, "Woolworths": 79.99},
    ),
    ("Coca-Cola Original", "2L", "Groceries", {"Checkers": 25.99, "Pick n Pay": 26.99}),
    ("Clover Full Cream Milk", "2L", "Fresh", {"Checkers": 36.99, "Pick n Pay": 38.49, "Woolworths": 41.99}),
    ("Clover Low Fat Milk", "2L", "Fresh", {"Checkers": 35.99, "Pick n Pay": 37.49, "Woolworths": 40.99}),
    ("Large Eggs", "18 pack", "Fresh", {"Checkers": 69.99, "Pick n Pay": 72.99, "Woolworths": 79.99}),
    ("Chicken Portions", "2kg", "Fresh", {"Checkers": 109.99, "Pick n Pay": 114.99, "Woolworths": 134.99}),
    ("Bananas", "per kg", "Fresh", {"Checkers": 24.99, "Pick n Pay": 26.99, "Woolworths": 29.99}),
    ("Clover Butter", "500g", "Fresh", {"Checkers": 84.99, "Pick n Pay": 89.99, "Woolworths": 99.99}),
    (
        "Castle Lite",
        "24 x 340ml",
        "Liquor",
        {"Ultra Liquors": 329.99, "Liquorland": 339.99, "Checkers": 344.99, "Pick n Pay": 349.99},
    ),
    (
        "Klipdrift Premium Brandy",
        "750ml",
        "Liquor",
        {"Ultra Liquors": 189.99, "Liquorland": 194.99, "Pick n Pay": 199.99},
    ),
    (
        "Savanna Dry Cider",
        "6 x 330ml",
        "Liquor",
        {"Ultra Liquors": 98.99, "Liquorland": 102.99, "Checkers": 104.99},
    ),
    (
        "Fat Bastard Chardonnay",
        "750ml",
        "Liquor",
        {"Ultra Liquors": 79.99, "Liquorland": 84.99, "Checkers": 89.99},
    ),
    (
        "Jameson Irish Whiskey",
        "750ml",
        "Liquor",
        {"Ultra Liquors": 369.99, "Liquorland": 379.99, "Pick n Pay": 389.99},
    ),
    (
        "Panado Tablets 500mg",
        "24 tablets",
        "Health & Beauty",
        {"Dis-Chem": 34.95, "Checkers": 38.99, "Pick n Pay": 39.99},
    ),
    (
        "Colgate Total Toothpaste",
        "100ml",
        "Health & Beauty",
        {"Dis-Chem": 31.95, "Checkers": 33.99, "Pick n Pay": 34.99},
    ),
    (
        "Dettol Antiseptic Liquid",
        "500ml",
        "Health & Beauty",
        {"Dis-Chem": 79.95, "Checkers": 84.99, "Pick n Pay": 86.99},
    ),
    (
        "Nivea Roll-on Deodorant",
        "50ml",
        "Health & Beauty",
        {"Dis-Chem": 39.95, "Checkers": 41.99, "Pick n Pay": 44.99},
    ),
    ("Vitamin C 1000mg", "30 tablets", "Health & Beauty", {"Dis-Chem": 89.95, "Checkers": 99.99}),
]


def store_name(name: str, store: str) -> str:
    """Write the name the way different shops do, so matching gets exercised."""
    if store == "Woolworths":
        return name.upper()
    if store == "Pick n Pay":
        return name.replace("Clover ", "Clover Fresh ") if name.startswith("Clover") else name
    return name


def rows_for(region_index: int, when: datetime, rng: random.Random) -> list[dict]:
    rows = []
    for name, size, cat, prices in SAMPLE:
        for store, price in prices.items():
            # Small made-up variation per region and day, so charts and towns have something to show.
            wobble = 1 + rng.uniform(-0.03, 0.03) + region_index * 0.01
            p = round(price * wobble, 2)
            promo = round(p * 0.9, 2) if rng.random() < 0.08 else None
            row = {
                "store": store,
                "name": store_name(name, store),
                "size": size,
                "category": cat,
                "price": p,
                "url": "",
            }
            if promo:
                row["promo_price"] = promo
            rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write example (made-up) price data for testing.")
    ap.add_argument("--regions", default="paarl")
    ap.add_argument("--days", type=int, default=7, help="days of example history")
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)
    settings = get_settings()
    rng = random.Random(args.seed)
    now = sa_now().replace(second=0, microsecond=0)
    regions = [r.strip() for r in args.regions.split(",") if r.strip()]

    if not args.no_db:
        from pricepulse.db import ScrapeRun, session_factory, utcnow
        from pricepulse.ingest import ingest_items, parse_scraped_at

        Session = session_factory(settings.database_url)

    for ri, region in enumerate(regions):
        for day in range(args.days - 1, -1, -1):
            when = now - timedelta(days=day)
            rows = rows_for(ri, when, rng)
            stamp = when.strftime("%Y-%m-%d %H:%M")
            if day == 0:
                write_atomic(
                    settings.data_dir / f"latest_{region}.json",
                    {"region": region, "scraped_at": stamp, "sample": True, "items": rows},
                )
            if not args.no_db:
                with Session.begin() as s:
                    at = parse_scraped_at(stamp)
                    rep = ingest_items(s, rows, region, at, settings, is_sample=True)
                    if day == 0:
                        for store in sorted({r["store"] for r in rows}):
                            n = sum(1 for r in rows if r["store"] == store)
                            s.add(
                                ScrapeRun(
                                    store=store,
                                    region=region,
                                    started_at=at,
                                    finished_at=utcnow(),
                                    status="sample",
                                    item_count=n,
                                )
                            )
                if day == 0:
                    print(f"{region}: {rep.summary()}")
        print(f"Wrote EXAMPLE data for {region} ({args.days} days). Not real prices.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
