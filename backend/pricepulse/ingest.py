"""Load scraper output (the latest_{region}.json contract) into the database."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .db import Listing, Price, Product, get_or_create_store, slot_for, utcnow
from .matching import Candidate, Normalised, match, normalise
from .money import clean_price, to_cents
from .sizes import Size, split_size

log = logging.getLogger("pricepulse.ingest")

SAST = timezone(timedelta(hours=2))  # South Africa has no daylight saving.


def sa_now() -> datetime:
    """South African time without tzinfo, as written in scraper files ("2026-10-27 07:30")."""
    return datetime.now(SAST).replace(tzinfo=None)


def parse_scraped_at(value: str | None) -> datetime:
    """Read a scraped_at stamp. Plain "2026-10-27 07:30" is South African time.

    Returns naive UTC. Falls back to now when missing or unreadable.
    """
    if not value:
        return utcnow()
    s = str(value).strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=SAST)
        return dt.astimezone(UTC).replace(tzinfo=None)
    return utcnow()


def read_scraper_json(data: Any) -> tuple[str, str, list[dict]]:
    """Accept {"region", "scraped_at", "items": [...]} or a bare list of rows."""
    if isinstance(data, list):
        return "", "", data
    if isinstance(data, dict):
        items = data.get("items") or data.get("products") or []
        return str(data.get("region", "")), str(data.get("scraped_at", "")), list(items)
    raise ValueError("Expected a JSON object with 'items' or a list of rows.")


@dataclass
class IngestReport:
    """What happened during one load. Printed in plain language."""

    rows: int = 0
    prices_written: int = 0
    new_products: int = 0
    matched: int = 0
    needs_review: int = 0
    rejected: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.rows} rows read, {self.prices_written} prices saved, "
            f"{self.matched} matched to existing products, {self.new_products} new products, "
            f"{self.needs_review} need review, {len(self.rejected)} rejected."
        )


def _norm_from_product(p: Product) -> Normalised:
    size = Size(qty=p.qty, unit=p.unit, pack=p.pack, by_weight=p.by_weight) if p.unit else None
    return Normalised(brand=p.brand_key, name=p.norm_name, size=size, generic=p.is_generic)


def _candidates(session: Session, size_key: str) -> list[Candidate]:
    rows = session.scalars(select(Product).where(Product.size_key == size_key)).all()
    out = []
    for p in rows:
        stores = {lst.store.name for lst in p.listings}
        out.append(Candidate(id=p.id, norm=_norm_from_product(p), stores=stores))
    return out


def display_name(name: str, size_text: str) -> str:
    """The name without its size: "Clover Milk 2L" -> "Clover Milk". Strengths like 500mg stay."""
    if size_text:
        low = name.lower()
        idx = low.rfind(size_text.lower())
        if idx >= 0:
            name = (name[:idx] + name[idx + len(size_text) :]).strip(" -,|")
    return " ".join(name.split())


def _new_product(session: Session, name: str, size_text: str, category: str, norm: Normalised) -> Product:
    display = display_name(name, size_text)
    s = norm.size
    p = Product(
        name=display or name,
        brand=norm.brand.title() if norm.brand else "",
        brand_key=norm.brand,
        size_text=size_text,
        size_key=norm.size_key,
        unit=s.unit if s else "",
        qty=s.qty if s else 0,
        pack=s.pack if s else 1,
        by_weight=s.by_weight if s else False,
        category=category or "Groceries",
        norm_name=norm.name,
        is_generic=norm.generic,
    )
    session.add(p)
    session.flush()
    return p


def ingest_items(
    session: Session,
    items: list[dict],
    region: str,
    scraped_at: datetime,
    settings: Settings,
    is_sample: bool = False,
) -> IngestReport:
    """Write rows into the database. Safe to re-run: same slot overwrites."""
    report = IngestReport()
    slot = slot_for(scraped_at, settings.slot_hours)
    for row in items:
        report.rows += 1
        name = str(row.get("name") or "").strip()
        store_name = str(row.get("store") or "").strip()
        price = clean_price(row.get("price"))
        if not name or not store_name or price is None:
            report.rejected.append(
                f"{store_name or '?'}: {name or '(no name)'} - missing name, store or price"
            )
            continue
        promo = clean_price(row.get("promo_price"))
        if promo is not None and promo >= price:
            promo = None
        size_text = str(row.get("size") or "").strip()
        if not size_text:
            _, size_text = split_size(name)
        url = str(row.get("url") or "").strip()[:400]
        category = str(row.get("category") or "").strip()
        ident = (url or f"{name}|{size_text}").lower()[:400]

        store = get_or_create_store(session, store_name)
        listing = session.scalar(select(Listing).where(Listing.store_id == store.id, Listing.ident == ident))
        if listing is None:
            norm = normalise(name, size_text, row.get("brand"))
            result = match(
                norm,
                _candidates(session, norm.size_key),
                store=store_name,
                auto_threshold=settings.match_auto,
                review_threshold=settings.match_review,
            )
            listing = Listing(
                store_id=store.id,
                ident=ident,
                name=name,
                size_text=size_text,
                url=url,
                category=category,
                match_status=result.status,
                match_score=result.score,
                match_reason=result.reason,
                suggestion_id=result.suggestion_id,
            )
            if result.product_id is not None:
                listing.product_id = result.product_id
                report.matched += 1
                product = session.get(Product, result.product_id)
                display = display_name(name, size_text)
                # Prefer a normally-cased name over an ALL CAPS one for display.
                if product and product.name.isupper() and not display.isupper():
                    product.name = display
            else:
                # Unmatched and review items still get their own product so the price shows.
                product = _new_product(session, name, size_text, category, norm)
                listing.product_id = product.id
                report.new_products += 1
                if result.status == "needs_review":
                    report.needs_review += 1
            session.add(listing)
            session.flush()
        else:
            listing.name, listing.size_text = name, size_text
            listing.url = url or listing.url
            listing.category = category or listing.category
            listing.last_seen = utcnow()

        existing = session.scalar(
            select(Price).where(Price.listing_id == listing.id, Price.region == region, Price.slot == slot)
        )
        if existing is None:
            existing = Price(listing_id=listing.id, region=region, slot=slot)
            session.add(existing)
        existing.price_cents = to_cents(price)
        existing.promo_cents = to_cents(promo)
        existing.scraped_at = scraped_at
        existing.is_sample = is_sample
        report.prices_written += 1
    session.flush()
    for r in report.rejected:
        log.info("Rejected row: %s", r)
    return report


def ingest_file(
    session: Session, path: Path, settings: Settings, region: str | None = None, is_sample: bool = False
) -> IngestReport:
    """Load one latest_{region}.json (or history) file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    file_region, stamp, items = read_scraper_json(data)
    if isinstance(data, dict) and data.get("sample"):
        is_sample = True
    reg = region or file_region
    if not reg:
        raise ValueError(f"{path}: no region in file; pass --region.")
    return ingest_items(session, items, reg, parse_scraped_at(stamp), settings, is_sample=is_sample)
