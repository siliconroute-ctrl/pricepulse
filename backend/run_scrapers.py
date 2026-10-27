"""Run the store scrapers and save prices.

Examples:
    python run_scrapers.py --region paarl
    python run_scrapers.py --region paarl --store Checkers --debug
    python run_scrapers.py --region all
    python run_scrapers.py --region paarl --every 6        # keep running every 6 hours

Writes data/latest_{region}.json (atomically), a timestamped history copy, a health
report data/health_{region}.json, and loads the prices into the database unless
--no-db is given.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from pricepulse.config import BACKEND_DIR, get_settings
from pricepulse.ingest import sa_now
from pricepulse.regions import load_regions, valid_region
from pricepulse.scrapers.base import BaseScraper, BlockedError, Item, StoreHealth, check_items
from pricepulse.scrapers.stores import ALL_SCRAPERS, find_scraper

log = logging.getLogger("pricepulse.run")


def read_terms(path: Path) -> list[tuple[str, str]]:
    """Read products.txt into (term, category) pairs. '# category: X' sets the category."""
    terms: list[tuple[str, str]] = []
    category = "Groceries"
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            body = line.lstrip("#").strip()
            if body.lower().startswith("category:"):
                category = body.split(":", 1)[1].strip() or "Groceries"
            continue
        terms.append((line, category))
    return terms


def write_atomic(path: Path, data: dict) -> None:
    """Write JSON to a temp file then rename, so readers never see a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def scrape_store(
    cls: type[BaseScraper], browser, region: str, setting: dict, terms: list[tuple[str, str]], args
) -> tuple[list[Item], StoreHealth]:
    """Run one store. Never raises: problems end up in the health report."""
    settings = get_settings()
    regions = load_regions(str(settings.regions_file))
    health = StoreHealth(
        store=cls.name,
        region=region,
        pricing=regions.pricing_mode(cls.name),
        terms_total=len(terms),
        started=sa_now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    scraper = cls(
        region=region,
        region_setting=setting,
        debug=args.debug,
        debug_dir=BACKEND_DIR / "debug",
        fixture_dir=(BACKEND_DIR / "tests" / "fixtures" / "live") if args.save_fixtures else None,
    )
    items: list[Item] = []
    shown = 0
    context = browser.new_context(
        locale="en-ZA",
        timezone_id="Africa/Johannesburg",
        user_agent=(
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Mobile Safari/537.36 PricePulseZA-price-check"
        ),
        viewport={"width": 412, "height": 915},
    )
    page = context.new_page()
    try:
        scraper.setup(page)
        for term, category in terms:
            try:
                found = scraper.search(page, term, category)
            except BlockedError as exc:
                health.status = "blocked"
                health.errors.append(f"Blocked: {exc}")
                health.terms_missing.extend(t for t, _ in terms[terms.index((term, category)) :])
                break
            except Exception as exc:  # noqa: BLE001 - one term must not stop the store
                health.errors.append(f"{term}: {exc}")
                found = []
            found = check_items(found, health)
            if found:
                health.terms_found += 1
                items.extend(found)
                for it in found:
                    if args.debug and shown < 5:
                        shown += 1
                        print(
                            f"   {it.name} | {it.size or '-'} | R{it.price:.2f}"
                            f"{f' (promo R{it.promo_price:.2f})' if it.promo_price else ''} | {it.url}"
                        )
            else:
                health.terms_missing.append(term)
            scraper.pause()
    except Exception as exc:  # noqa: BLE001 - setup failure fails this store only
        health.status = "failed"
        health.errors.append(f"Could not open the website: {exc}")
    finally:
        context.close()
    health.items = len(items)
    health.finish()
    return items, health


def previous_rows(path: Path, store: str) -> list[dict]:
    """Rows for ``store`` from the last good file, kept when this run failed."""
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    stamp = data.get("scraped_at", "")
    out = []
    for row in data.get("items", []):
        if row.get("store") == store:
            row.setdefault("scraped_at", stamp)
            out.append(row)
    return out


def other_stores(path: Path, ran: set[str]) -> list[str]:
    """Stores in the last file that were not part of this run."""
    if not path.is_file():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("items", [])
    except (ValueError, AttributeError):
        return []
    return sorted({r.get("store", "") for r in rows} - ran - {""})


def run_once(args) -> int:
    """One full run. Returns 0 when at least one store worked."""
    settings = get_settings()
    regions = load_regions(str(settings.regions_file))
    terms = read_terms(Path(args.products))
    if args.limit:
        terms = terms[: args.limit]
    if args.region == "all":
        region_list = list(regions.regions)
    else:
        region_list = [args.region]
    classes = ALL_SCRAPERS
    if args.store:
        classes = []
        for name in args.store:
            cls = find_scraper(name)
            if not cls:
                print(f"Unknown store '{name}'. Known: {', '.join(c.name for c in ALL_SCRAPERS)}")
                return 2
            classes.append(cls)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "Playwright is not installed. Run: pip install -r requirements-scraper.txt "
            "then: python -m playwright install chromium"
        )
        return 2

    any_ok = False
    national_cache: dict[str, tuple[list[Item], StoreHealth]] = {}
    with sync_playwright() as pw:
        launch = {"headless": not args.show}
        if os.environ.get("PP_CHROMIUM_PATH"):
            launch["executable_path"] = os.environ["PP_CHROMIUM_PATH"]
        browser = pw.chromium.launch(**launch)
        for region in region_list:
            started = sa_now()
            print(f"\nPricePulse scrape for {regions.regions[region].name} - {started:%d %b %Y, %H:%M}")
            all_rows: list[dict] = []
            healths: list[StoreHealth] = []
            latest = settings.data_dir / f"latest_{region}.json"
            for cls in classes:
                mode = regions.pricing_mode(cls.name)
                setting = regions.store_setting(region, cls.name)
                if mode in ("national", "unknown") and cls.name in national_cache:
                    items, health0 = national_cache[cls.name]
                    health = StoreHealth(**{**health0.__dict__, "region": region})
                    print(f"  {cls.name}: same prices nationally, reusing the first region's results.")
                else:
                    print(f"  {cls.name}: searching {len(terms)} products...")
                    items, health = scrape_store(cls, browser, region, setting, terms, args)
                    if mode in ("national", "unknown"):
                        national_cache[cls.name] = (items, health)
                    # Stagger stores with a random pause.
                    time.sleep(random.uniform(2, 6))
                healths.append(health)
                print("  " + health.summary())
                if items:
                    all_rows.extend(i.to_dict() for i in items)
                    any_ok = True
                else:
                    kept = previous_rows(latest, cls.name)
                    if kept:
                        print(f"  {cls.name}: kept {len(kept)} prices from the last good run.")
                        all_rows.extend(kept)
            # Stores not part of this run (e.g. --store Checkers) keep their last rows.
            ran = {c.name for c in classes}
            for name in other_stores(latest, ran):
                all_rows.extend(previous_rows(latest, name))
            stamp = started.strftime("%Y-%m-%d %H:%M")
            payload = {"region": region, "scraped_at": stamp, "items": all_rows}
            if all_rows:
                write_atomic(latest, payload)
                hist = settings.data_dir / "history" / f"{region}_{started:%Y%m%d_%H%M}.json"
                write_atomic(hist, payload)
            else:
                print("  Nothing found at any store. The previous price file was left as it was.")
            report = {
                "region": region,
                "started": started.isoformat(timespec="seconds"),
                "stores": [h.__dict__ for h in healths],
            }
            write_atomic(settings.data_dir / f"health_{region}.json", report)
            if not args.no_db and all_rows:
                load_into_db(payload, healths, settings)
            print_report(region, healths, all_rows)
        browser.close()
    return 0 if any_ok else 1


def load_into_db(payload: dict, healths: list[StoreHealth], settings) -> None:
    from pricepulse.db import ScrapeRun, session_factory, utcnow
    from pricepulse.ingest import ingest_items, parse_scraped_at

    Session = session_factory(settings.database_url)
    with Session.begin() as s:
        when = parse_scraped_at(payload["scraped_at"])
        fresh = [r for r in payload["items"] if "scraped_at" not in r]
        report = ingest_items(s, fresh, payload["region"], when, settings)
        for h in healths:
            s.add(
                ScrapeRun(
                    store=h.store,
                    region=h.region,
                    started_at=when,
                    finished_at=utcnow(),
                    status=h.status,
                    item_count=h.items,
                    terms_total=h.terms_total,
                    terms_missing="\n".join(h.terms_missing),
                    error="\n".join(h.errors)[:4000],
                )
            )
    print("  Database: " + report.summary())


def print_report(region: str, healths: list[StoreHealth], rows: list[dict]) -> None:
    stores = {r["store"] for r in rows}
    suspect = sum(len(h.suspect) for h in healths)
    print(f"\nSummary for {region}: {len(rows)} prices from {len(stores)} stores.")
    if suspect:
        print(f"  {suspect} prices look wrong (under R1 or over R5000). Check data/health_{region}.json.")
    blocked = [h.store for h in healths if h.status == "blocked"]
    if blocked:
        print("  Blocked by: " + ", ".join(blocked) + ". These were skipped; the others carried on.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scrape store prices for PricePulse ZA.")
    ap.add_argument("--region", default="paarl", help="region name from regions.yaml, or 'all'")
    ap.add_argument("--store", action="append", help="only this store (repeat for more)")
    ap.add_argument("--products", default=str(BACKEND_DIR / "products.txt"))
    ap.add_argument("--limit", type=int, default=0, help="only the first N search terms")
    ap.add_argument(
        "--debug", action="store_true", help="print the first rows and save pages when nothing is found"
    )
    ap.add_argument("--save-fixtures", action="store_true", help="save raw pages to tests/fixtures/live/")
    ap.add_argument("--show", action="store_true", help="show the browser window")
    ap.add_argument("--no-db", action="store_true", help="only write the JSON files")
    ap.add_argument("--every", type=float, default=0, help="repeat every N hours")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )
    settings = get_settings()
    regions = load_regions(str(settings.regions_file))
    if args.region != "all" and (not valid_region(args.region) or args.region not in regions.regions):
        print(f"Unknown region '{args.region}'. Known: {', '.join(regions.regions)}")
        return 2
    if not args.every:
        return run_once(args)
    while True:
        code = run_once(args)
        wait = args.every * 3600 + random.uniform(0, 600)
        nxt = sa_now() + timedelta(seconds=wait)
        print(f"Next run at {nxt:%H:%M} South African time (exit code this run: {code}).")
        time.sleep(wait)


if __name__ == "__main__":
    sys.exit(main())
