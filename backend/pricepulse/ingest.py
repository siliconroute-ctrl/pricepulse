"""Load scraper output (the latest_{region}.json contract) into the database."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, selectinload

from .categories import adjust_category
from .config import Settings
from .db import Listing, Price, Product, ScrapeRun, SessionItem, get_or_create_store, slot_for, utcnow
from .matching import HOUSE_BRANDS, Candidate, Normalised, compatible, match, neutral_name, normalise, score
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
    by_barcode: int = 0
    rejected: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.rows} rows read, {self.prices_written} prices saved, "
            f"{self.matched} matched to existing products, {self.new_products} new products, "
            f"{self.needs_review} need review, {len(self.rejected)} rejected."
            + (f" {self.by_barcode} joined by barcode." if self.by_barcode else "")
        )


def _norm_from_product(p: Product) -> Normalised:
    size = Size(qty=p.qty, unit=p.unit, pack=p.pack, by_weight=p.by_weight) if p.unit else None
    return Normalised(brand=p.brand_key, name=p.norm_name, size=size, generic=p.is_generic)


def _candidates(session: Session, size_key: str) -> list[Candidate]:
    if not size_key:
        return []  # nothing can be matched without a size (match() sends such items to review anyway)
    rows = session.scalars(
        select(Product)
        .where(Product.size_key == size_key)
        .options(selectinload(Product.listings).selectinload(Listing.store))
    ).all()
    out = []
    for p in rows:
        stores = {lst.store.name for lst in p.listings}
        out.append(Candidate(id=p.id, norm=_norm_from_product(p), stores=stores))
    return out


def clean_barcode(value: Any) -> str:
    """Digits of a barcode, or "" when it is not a plausible one (wrong length, 0000000000000...)."""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) in (8, 12, 13, 14) and len(set(digits)) > 1 else ""


def _barcode_product(
    session: Session, listing: Listing | None, barcode: str, store_id: int
) -> Product | None:
    """The product another store lists under this same barcode, if joining it is safe.

    Safe means: that store's listing is at a different store, the product has no listing from
    this store yet, and the two sizes (when both are known) agree. A barcode names one exact
    product, so this never joins two different things.
    """
    peers = session.scalars(
        select(Listing)
        .where(Listing.barcode == barcode, Listing.store_id != store_id, Listing.product_id.is_not(None))
        .order_by(Listing.id)
    ).all()
    for peer in peers:
        product = session.get(Product, peer.product_id)
        if product is None:
            continue
        if any(other.store_id == store_id and other is not listing for other in product.listings):
            continue
        if listing is not None and listing.product_id is not None:
            mine = session.get(Product, listing.product_id)
            if mine and mine.size_key and product.size_key and mine.size_key != product.size_key:
                continue
        return product
    return None


def _retire_product(session: Session, old_id: int, new_id: int) -> None:
    """Remove a product that has no listings left, moving anything that pointed at it."""
    if session.scalar(select(Listing.id).where(Listing.product_id == old_id).limit(1)) is not None:
        return
    session.execute(update(Listing).where(Listing.suggestion_id == old_id).values(suggestion_id=None))
    for item in session.scalars(select(SessionItem).where(SessionItem.product_id == old_id)).all():
        clash = session.scalar(
            select(SessionItem.id).where(
                SessionItem.session_id == item.session_id, SessionItem.product_id == new_id
            )
        )
        if clash is None:
            item.product_id = new_id
        else:
            session.execute(delete(SessionItem).where(SessionItem.id == item.id))
    session.flush()
    session.execute(delete(Product).where(Product.id == old_id))


def _join_by_barcode(
    session: Session, listing: Listing, barcode: str, store_id: int, report: IngestReport
) -> None:
    """An older listing just learned its barcode: join the other store's product when safe.

    Only a listing that sits alone in its own product is moved (a product already shared by
    name matching is left as it is). The emptied product is removed.
    """
    old_id = listing.product_id
    others = session.scalars(
        select(Listing).where(Listing.product_id == old_id, Listing.id != listing.id)
    ).all()
    if old_id is None or others:
        return
    target = _barcode_product(session, listing, barcode, store_id)
    if target is None or target.id == old_id:
        return
    listing.product_id = target.id
    listing.match_status, listing.match_score = "exact", 100.0
    listing.match_reason, listing.suggestion_id = "same barcode", None
    session.flush()
    _retire_product(session, old_id, target.id)
    report.by_barcode += 1


def display_name(name: str, size_text: str) -> str:
    """The name without its size: "Clover Milk 2L" -> "Clover Milk". Strengths like 500mg stay."""
    if size_text:
        low = name.lower()
        idx = low.rfind(size_text.lower())
        if idx >= 0:
            name = (name[:idx] + name[idx + len(size_text) :]).strip(" -,|")
    return " ".join(name.split())


def _new_product(
    session: Session, name: str, size_text: str, category: str, norm: Normalised, via_search: bool = False
) -> Product:
    display = display_name(name, size_text)
    house_produce = norm.generic and norm.brand in HOUSE_BRANDS
    if house_produce:
        display = neutral_name(display)
    s = norm.size
    p = Product(
        name=display or name,
        brand="" if house_produce else (norm.brand.title() if norm.brand else ""),
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
        via_search=via_search,
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
        category = adjust_category(name, category)
        ident = str(row.get("ident") or url or f"{name}|{size_text}").lower()[:400]
        sku = str(row.get("sku") or "").strip()[:40]
        department = str(row.get("department") or "").strip()[:60]
        condition = str(row.get("condition") or "").strip()[:12]
        description = str(row.get("description") or "").strip()[:500]
        image_url = str(row.get("image_url") or "").strip()[:400]
        source = str(row.get("source") or "scrape")[:8]

        store = get_or_create_store(session, store_name)
        listing = session.scalar(select(Listing).where(Listing.store_id == store.id, Listing.ident == ident))
        barcode = clean_barcode(row.get("barcode"))
        try:
            hit_rank = int(row.get("rank")) if row.get("rank") else None
        except (TypeError, ValueError):
            hit_rank = None
        hit_term = str(row.get("term") or "").strip()[:80]
        if listing is None:
            norm = normalise(name, size_text, row.get("brand"))
            by_code = _barcode_product(session, None, barcode, store.id) if barcode else None
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
                barcode=barcode,
                hit_rank=hit_rank,
                term=hit_term,
                sku=sku,
                department=department,
                condition=condition,
                description=description,
                image_url=image_url,
                source=source,
                active=True,
            )
            if by_code is not None:
                listing.product_id = by_code.id
                listing.match_status, listing.match_score = "exact", 100.0
                listing.match_reason, listing.suggestion_id = "same barcode", None
                report.matched += 1
                report.by_barcode += 1
            elif result.product_id is not None:
                listing.product_id = result.product_id
                report.matched += 1
                product = session.get(Product, result.product_id)
                display = display_name(name, size_text)
                # Prefer a normally-cased name over an ALL CAPS one for display.
                if product and product.name.isupper() and not display.isupper():
                    product.name = display
            else:
                # Unmatched and review items still get their own product so the price shows.
                product = _new_product(
                    session, name, size_text, category, norm, via_search=bool(row.get("new"))
                )
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
            listing.active = True  # seen in the latest list again
            if sku:
                listing.sku = sku
            if department:
                listing.department = department
            if condition:
                listing.condition = condition
            if description:
                listing.description = description
            if image_url:
                listing.image_url = image_url
            if hit_rank:
                listing.hit_rank, listing.term = hit_rank, hit_term
            if barcode and not listing.barcode:
                listing.barcode = barcode
                _join_by_barcode(session, listing, barcode, store.id, report)

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
    when = parse_scraped_at(stamp)
    report = ingest_items(session, items, reg, when, settings, is_sample=is_sample)
    if not is_sample:
        record_runs(session, items, reg, when)
    return report


@dataclass
class RematchReport:
    refreshed: int = 0  # products whose size was read again and changed
    joined: int = 0  # single-listing products folded into another store's product
    joined_by_barcode: int = 0
    merged: int = 0  # products that already held several listings, folded into another store's product
    review_before: int = 0
    review_after: int = 0

    def summary(self) -> str:
        return (
            f"{self.refreshed} product records refreshed, {self.joined} products joined "
            f"({self.joined_by_barcode} by barcode), {self.merged} merged, "
            f"review queue {self.review_before} -> {self.review_after}."
        )


def rematch(session: Session, settings: Settings) -> RematchReport:
    """Run today's matching rules over the listings already loaded, without losing anything.

    Prices, history, accounts and lookup sessions stay. Only product groupings change:
    - every product's size is read again with the current size rules;
    - a product that holds one listing is joined to another store's product when the barcode
      is the same or the match is now confident; a lookup-session item moves to the joined product;
    - decisions the owner made in the review queue (status ``manual``) are never changed, and a
      listing kept separate (``new``) is only joined on a barcode or a confident match.
    Run it inside a transaction and roll back to preview.
    """
    rep = RematchReport()
    rep.review_before = (
        session.scalar(
            select(func.count()).select_from(Listing).where(Listing.match_status == "needs_review")
        )
        or 0
    )
    # 1. Read every product's size again.
    for p in session.scalars(select(Product).order_by(Product.id)).all():
        first = p.listings[0] if p.listings else None
        if first is None:
            continue
        size_text = first.size_text or split_size(first.name)[1]
        norm = normalise(first.name, size_text, p.brand or None)
        new = (
            norm.size_key,
            norm.size.unit if norm.size else "",
            norm.size.qty if norm.size else 0,
            norm.size.pack if norm.size else 1,
            norm.size.by_weight if norm.size else False,
        )
        if new != (p.size_key, p.unit, p.qty, p.pack, p.by_weight):
            p.size_key, p.unit, p.qty, p.pack, p.by_weight = new
            p.size_text = size_text or p.size_text
            rep.refreshed += 1
        # The words used for matching are stored on the product when it is made, so they must be
        # read again too, or products made under older rules (brand, "generic" produce, packaging
        # words) never match newer listings.
        words = (norm.brand, norm.name, norm.generic)
        if words != (p.brand_key, p.norm_name, p.is_generic):
            p.brand_key, p.norm_name, p.is_generic = words
            rep.refreshed += 1
        # Category fixes (for example flavoured water found by a fruit search): products and listings.
        fixed = adjust_category(first.name, p.category)
        if fixed != p.category:
            p.category = fixed
            rep.refreshed += 1
        for lst in p.listings:
            lst.category = adjust_category(lst.name, lst.category)
    session.flush()
    # 2. Join lonely products to other stores' products.
    for p in session.scalars(select(Product).order_by(Product.id)).all():
        if session.get(Product, p.id) is None or len(p.listings) != 1:
            continue
        lst = p.listings[0]
        if lst.match_status == "manual":
            continue
        store = lst.store
        target: Product | None = None
        by_code = False
        if lst.barcode:
            target = _barcode_product(session, lst, lst.barcode, store.id)
            by_code = target is not None
        result = None
        if target is None:
            size_text = lst.size_text or split_size(lst.name)[1]
            norm = normalise(lst.name, size_text, p.brand or None)
            cands = [c for c in _candidates(session, norm.size_key) if c.id != p.id]
            result = match(
                norm,
                cands,
                store=store.name,
                auto_threshold=settings.match_auto,
                review_threshold=settings.match_review,
            )
            if result.product_id is not None:
                target = session.get(Product, result.product_id)
        if target is not None and target.id != p.id:
            lst.product_id = target.id
            lst.match_status, lst.suggestion_id = ("exact", None) if by_code else (result.status, None)
            lst.match_score = 100.0 if by_code else result.score
            lst.match_reason = "same barcode" if by_code else result.reason
            session.flush()
            _retire_product(session, p.id, target.id)
            rep.joined += 1
            rep.joined_by_barcode += int(by_code)
        elif result is not None and lst.match_status == "needs_review":
            lst.match_status, lst.match_score = result.status, result.score
            lst.match_reason, lst.suggestion_id = result.reason, result.suggestion_id
    session.flush()
    _merge_pass(session, settings, rep)
    for p in session.scalars(select(Product).where(Product.is_generic.is_(True))).all():
        if p.brand_key in HOUSE_BRANDS:  # a shared card for plain produce carries no store label
            tidy = neutral_name(p.name)
            if tidy != p.name or p.brand:
                p.name, p.brand = tidy, ""
    rep.review_after = (
        session.scalar(
            select(func.count()).select_from(Listing).where(Listing.match_status == "needs_review")
        )
        or 0
    )
    return rep


def _same_item(a: Normalised, b: Normalised, auto: float) -> bool:
    """Would the matcher join these two without asking? (Same rules as ``match``.)"""
    if not compatible(a, b):
        return False
    if a.key == b.key:
        return True
    if not (a.brand or a.generic):
        return False  # unknown brand: only an identical name is safe
    return score(a, b) >= auto


def _merge_pass(session: Session, settings: Settings, rep: RematchReport) -> None:
    """Fold together products that sell the same item in different stores, even when they already
    hold several listings. A product is never merged with another that shares a store (a store does
    not sell one product twice), and products holding an answer the owner gave by hand are left alone.
    """
    groups: dict[str, list[Product]] = {}
    for p in session.scalars(select(Product).order_by(Product.id)).all():
        if p.size_key and p.listings:
            groups.setdefault(p.size_key, []).append(p)
    for group in groups.values():
        changed = True
        while changed:
            changed = False
            alive = [p for p in group if session.get(Product, p.id) is not None]
            for i, a in enumerate(alive):
                for b in alive[i + 1 :]:
                    if session.get(Product, b.id) is None or session.get(Product, a.id) is None:
                        continue
                    la, lb = list(a.listings), list(b.listings)
                    if not la or not lb or {x.store_id for x in la} & {x.store_id for x in lb}:
                        continue
                    if any(x.match_status == "manual" for x in la + lb):
                        continue
                    if not _same_item(_norm_from_product(a), _norm_from_product(b), settings.match_auto):
                        continue
                    for x in lb:
                        x.product_id = a.id
                        x.match_status, x.suggestion_id = "fuzzy", None
                        x.match_reason = "merged by rematch"
                    session.flush()
                    session.expire(a)
                    session.expire(b)
                    _retire_product(session, b.id, a.id)
                    rep.merged += 1
                    changed = True
        session.flush()


def record_runs(session: Session, items: list[dict], region: str, when: datetime) -> None:
    """One "ok" scrape record per store in a loaded file, so /api/v1/health reports real prices.

    Files loaded by ``admin.py ingest`` (the upload from the owner's PC) carry no run report of
    their own, and the health page reads these records. The time is when the prices were scraped,
    not when they were loaded, so "stale" still means old prices.
    """
    counts: dict[str, int] = {}
    for row in items:
        store = str(row.get("store") or "").strip()
        if store and clean_price(row.get("price")) is not None:
            counts[store] = counts.get(store, 0) + 1
    for store, n in sorted(counts.items()):
        session.add(
            ScrapeRun(
                store=store, region=region, started_at=when, finished_at=when, status="ok", item_count=n
            )
        )
