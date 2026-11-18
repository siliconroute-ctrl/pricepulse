"""Local (private) stores: the weekly list a store owner sends, checked and loaded.

A local store belongs to one region and is only seen there. Its owner sends a spreadsheet (CSV) of
the items they sell most, once a week, with their own selling prices. Each file REPLACES the store's
list: an item missing from the new file is hidden from shoppers (its price history is kept). Items are
matched to the chains' products by barcode, then by name, so one card can show a local store's price
next to Checkers and Woolworths. The same path will serve the store-owner API.

CSV columns (any order, header names are forgiving; only item name and price are required):
    item (or name), price (selling price), size, barcode, sku (the store's own item code),
    department (e.g. Butchery, Bakery), category (Groceries, Fresh, Health & Beauty, Liquor),
    promo price (a special price, lower than price).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings
from .db import Listing, Store, StoreSubmission, utcnow
from .essentials import CATEGORIES, clean_category
from .ingest import clean_barcode, ingest_items
from .money import clean_price

MAX_ROWS = 5000
MAX_PRICE = 100_000.0
MIN_PRICE = 0.5

HEADS = {
    "name": ["item", "item name", "name", "product", "description", "product name", "item description"],
    "price": ["price", "selling price", "sell price", "sales price", "retail price", "selling", "price r"],
    "promo": ["promo price", "promo", "special price", "special", "sale price", "was price"],
    "size": ["size", "pack size", "pack", "unit size", "weight", "volume"],
    "barcode": ["barcode", "ean", "upc", "gtin", "bar code"],
    "sku": ["sku", "item code", "product code", "code", "stock code", "plu"],
    "department": ["department", "dept", "section", "aisle"],
    "category": ["category", "type", "class"],
}
LABEL = {"name": "item name", "price": "price"}


def _norm_head(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).strip()


def parse_csv(text: str) -> tuple[list[dict], list[str]]:
    """CSV text to raw row dicts (keys: name, price, promo, size, barcode, sku, department, category).

    Returns (rows, problems). ``problems`` explains file-level trouble such as a missing column.
    """
    text = (text or "").lstrip("﻿")
    if not text.strip():
        return [], ["The file is empty."]
    first = text.splitlines()[0]
    sep = ","
    for c in (";", "\t"):
        if first.count(c) > first.count(sep):
            sep = c
    table = [r for r in csv.reader(io.StringIO(text), delimiter=sep) if any(x.strip() for x in r)]
    if not table:
        return [], ["The file has no rows."]
    head = [_norm_head(h) for h in table[0]]
    cols: dict[str, int] = {}
    for field_name, names in HEADS.items():
        for i, h in enumerate(head):
            if h in names and i not in cols.values():
                cols[field_name] = i
                break
    problems = []
    for need in ("name", "price"):
        if need not in cols:
            problems.append(f"There is no '{LABEL[need]}' column. The first row must name the columns.")
    if problems:
        return [], problems
    rows = []
    for r in table[1:]:
        row = {k: (r[i].strip() if i < len(r) else "") for k, i in cols.items()}
        if any(row.values()):
            rows.append(row)
    if len(rows) > MAX_ROWS:
        return [], [f"The file has {len(rows)} rows. Please send up to {MAX_ROWS} at a time."]
    return rows, []


@dataclass
class CleanRows:
    ok: list[dict] = field(default_factory=list)
    invalid: list[dict] = field(default_factory=list)  # {"row", "name", "why"}
    duplicates: int = 0


def _money(value: str) -> float | None:
    v = re.sub(r"(?i)\s|^r(?=\d)", "", value or "")
    if "," in v and "." not in v:
        v = v.replace(",", ".")  # 12,50
    else:
        v = v.replace(",", "")
    return clean_price(v)


def check_rows(rows: list[dict]) -> CleanRows:
    """Check every row. Good rows come back shaped for ``ingest_items``; bad ones are explained."""
    out = CleanRows()
    seen: set[str] = set()
    for i, raw in enumerate(rows, 2):  # row 1 is the header
        name = re.sub(r"\s+", " ", raw.get("name", "")).strip()
        if len(name) < 2 or len(name) > 120:
            out.invalid.append(
                {"row": i, "name": name[:60], "why": "the item name must be 2 to 120 characters"}
            )
            continue
        if re.search(r"https?:|www\.|@", name, re.I):
            out.invalid.append(
                {"row": i, "name": name[:60], "why": "the item name must not contain a web address"}
            )
            continue
        price = _money(raw.get("price", ""))
        if price is None or not (MIN_PRICE <= price <= MAX_PRICE):
            out.invalid.append(
                {
                    "row": i,
                    "name": name[:60],
                    "why": f"the price must be a number from R{MIN_PRICE:.2f} to R{MAX_PRICE:,.0f}",
                }
            )
            continue
        promo = _money(raw.get("promo", "")) if raw.get("promo") else None
        if promo is not None and not (MIN_PRICE <= promo < price):
            promo = None  # a promo that is not lower than the price is ignored, not an error
        try:
            category = clean_category(raw.get("category", ""))
        except ValueError:
            out.invalid.append(
                {"row": i, "name": name[:60], "why": "category must be one of " + ", ".join(CATEGORIES)}
            )
            continue
        barcode = clean_barcode(raw.get("barcode", ""))
        sku = re.sub(r"\s+", "", raw.get("sku", ""))[:40]
        key = f"sku:{sku.lower()}" if sku else f"{name.lower()}|{raw.get('size', '').strip().lower()}"
        if key in seen:
            out.duplicates += 1
            continue
        seen.add(key)
        out.ok.append(
            {
                "name": name,
                "size": raw.get("size", "").strip()[:40],
                "price": price,
                "promo_price": promo,
                "barcode": barcode,
                "sku": sku,
                "department": re.sub(r"\s+", " ", raw.get("department", "")).strip()[:60],
                "category": category,
                "ident": f"{key}",
            }
        )
    return out


@dataclass
class ImportReport:
    rows: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    invalid: list[dict] = field(default_factory=list)
    duplicates: int = 0
    dry_run: bool = False
    matched_to_chains: int = 0

    def as_dict(self) -> dict:
        return {
            "rows": self.rows,
            "added": self.added,
            "updated": self.updated,
            "removed": self.removed,
            "duplicates": self.duplicates,
            "invalid_count": len(self.invalid),
            "invalid": self.invalid[:50],
            "matched_to_chains": self.matched_to_chains,
            "dry_run": self.dry_run,
        }


def import_for_store(
    s: Session,
    store: Store,
    rows: list[dict],
    settings: Settings,
    *,
    source: str = "csv",
    filename: str = "",
    replace: bool = True,
    by: str = "",
    dry_run: bool = False,
    now: datetime | None = None,
) -> ImportReport:
    """Load a store's weekly list. With ``replace`` the store's items not in this list are hidden.

    With ``dry_run`` nothing is kept: the caller gets the same numbers to show as a preview.
    """
    if store.kind != "local" or not store.region:
        raise ValueError("That store is not a local store.")
    now = now or utcnow()
    checked = check_rows(rows)
    rep = ImportReport(
        rows=len(rows), invalid=checked.invalid, duplicates=checked.duplicates, dry_run=dry_run
    )
    before = {lst.ident: lst for lst in s.scalars(select(Listing).where(Listing.store_id == store.id)).all()}
    items = [
        {**r, "store": store.name, "source": source, "rank": 1, "url": ""}  # local items are always shown
        for r in checked.ok
    ]
    if items:
        ingest_items(s, items, store.region, now, settings)
    s.flush()
    seen = {str(r["ident"]).lower()[:400] for r in checked.ok}
    rep.added = sum(1 for i in seen if i not in before)
    rep.updated = len(seen) - rep.added
    if replace and seen:
        stale = [lst for ident, lst in before.items() if ident not in seen and lst.active]
        for lst in stale:
            lst.active = False
        rep.removed = len(stale)
    # How many of the items sit on a product another store already sells (the point of the exercise).
    if seen:
        rep.matched_to_chains = _matched(s, store, seen)
    if dry_run:
        s.rollback()
        return rep
    store.last_submission_at = now
    s.add(
        StoreSubmission(
            store_id=store.id,
            received_at=now,
            source=source,
            filename=filename[:120],
            rows=rep.rows,
            added=rep.added,
            updated=rep.updated,
            removed=rep.removed,
            invalid=len(rep.invalid),
            replace=replace,
            by=by[:128],
        )
    )
    s.flush()
    return rep


def _matched(s: Session, store: Store, idents: set[str]) -> int:
    mine = s.scalars(
        select(Listing).where(Listing.store_id == store.id, Listing.ident.in_(list(idents)))
    ).all()
    count = 0
    for lst in mine:
        other = s.scalar(
            select(func.count())
            .select_from(Listing)
            .where(
                Listing.product_id == lst.product_id, Listing.store_id != store.id, Listing.active.is_(True)
            )
        )
        count += 1 if other else 0
    return count
