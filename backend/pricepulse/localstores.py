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
from .essentials import CATEGORIES
from .ingest import clean_barcode, ingest_items
from .money import clean_price

MAX_ROWS = 5000
MAX_PRICE = 100_000.0
MIN_PRICE = 0.5

# Header names a file may use, best name first. Matching ignores capitals, spaces and punctuation,
# so "PriceInclVAT", "Price incl. VAT" and "price_incl_vat" are the same. For each of our fields the
# BEST name wins, not the leftmost column ("Description" is the item name even if a "Product" code
# column comes first).
HEADS = {
    "name": [
        "item name",
        "item",
        "name",
        "product name",
        "description",
        "item description",
        "product description",
        "product",
    ],
    "price": [
        "selling price",
        "price",
        "sell price",
        "sales price",
        "retail price",
        "price incl vat",
        "priceinclvat",
        "price inc vat",
        "price including vat",
        "incl vat",
        "inc vat",
        "price r",
        "selling",
        "price excl vat",
        "priceexclvat",
    ],
    "promo": ["promo price", "promo", "special price", "special", "sale price", "was price"],
    "size": ["size", "pack size", "pack", "unit size", "weight", "volume"],
    "barcode": ["barcode", "ean", "upc", "gtin", "bar code"],
    "sku": ["sku", "item code", "product code", "stock code", "code", "plu", "part number", "product"],
    "department": ["department", "dept", "section", "aisle", "subcategory", "sub category", "sub-category"],
    "category": ["category", "type", "class", "main category"],
    "brand": ["brand", "manufacturer", "make"],
    "image": [
        "image url",
        "imageurl",
        "image link",
        "image",
        "picture url",
        "picture",
        "photo url",
        "photo",
        "product image",
    ],
    "stock": ["stock level", "stocklevel", "stock status", "in stock", "availability", "stock", "on hand"],
}
FIELDS = set(HEADS)
LABEL = {"name": "item name", "price": "price"}


def _norm_head(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).strip()


def _separator(text: str) -> str:
    """Comma, semicolon or tab: the one that appears on every one of the first lines."""
    lines = [ln for ln in text.splitlines()[:12] if ln.strip()]
    best, best_min = ",", 0
    for c in (",", ";", "\t"):
        low = min([ln.count(c) for ln in lines] or [0])
        if low > best_min:
            best, best_min = c, low
    if best_min == 0:  # none on every line: take the commonest one in the first line
        first = lines[0] if lines else ""
        best = max((",", ";", "\t"), key=first.count)
    return best


def parse_csv(text: str) -> tuple[list[dict], list[str]]:
    """CSV or tab-separated text to raw row dicts (keys: name, price, promo, size, barcode, sku,
    department, category, brand, stock).

    The first row must name the columns.
    Returns (rows, problems). ``problems`` explains file-level trouble such as a missing column.
    """
    text = (text or "").lstrip("\ufeff")
    if not text.strip():
        return [], ["The file is empty."]
    sep = _separator(text)
    table = [r for r in csv.reader(io.StringIO(text), delimiter=sep) if any(x.strip() for x in r)]
    if not table:
        return [], ["The file has no rows."]
    head = [re.sub(r"[^a-z0-9]", "", h.lower()) for h in table[0]]
    cols: dict[str, int] = {}
    for field_name, names in HEADS.items():
        for n in names:  # best name first
            key = re.sub(r"[^a-z0-9]", "", n.lower())
            hit = next((i for i, h in enumerate(head) if h == key and i not in cols.values()), None)
            if hit is not None:
                cols[field_name] = hit
                break
    body = table[1:]
    problems = []
    for need in ("name", "price"):
        if need not in cols:
            problems.append(f"There is no '{LABEL[need]}' column. The first row must name the columns.")
    if problems:
        return [], problems
    rows = []
    for r in body:
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
    out_of_stock: int = 0  # rows the store marked as not in stock: left out, so not shown
    new_categories: list[str] = field(default_factory=list)  # categories outside our four
    pictures: int = 0  # rows with a usable picture link
    bad_pictures: int = 0  # rows whose picture link was not usable (the item still loads)


def _money(value: str) -> float | None:
    v = re.sub(r"(?i)\s|^r(?=\d)", "", value or "")
    if "," in v and "." not in v:
        v = v.replace(",", ".")  # 12,50
    else:
        v = v.replace(",", "")
    return clean_price(v)


_NO_STOCK = re.compile(
    r"(?i)\b(no stock|out of stock|unavailable|sold out|not available|discontinued|backorder)\b"
    r"|^(0|no|n|false)$"
)
KNOWN_CATEGORIES = {c.lower(): c for c in CATEGORIES}


def in_stock(value: str) -> bool:
    """Blank, or anything that is not clearly "no stock", counts as in stock."""
    return not _NO_STOCK.search((value or "").strip())


def store_category(text: str) -> str:
    """Our four categories keep their names; other text (a wholesaler's own sections) is kept as typed."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return "Groceries"
    known = KNOWN_CATEGORIES.get(t.lower()) or KNOWN_CATEGORIES.get(t.lower().replace(" and ", " & "))
    if known:
        return known
    if len(t) > 40 or not re.fullmatch(r"[\w &/,'()+-]+", t):
        raise ValueError("the category must be plain text of up to 40 characters")
    return t


def clean_image_url(value: str) -> str:
    """A picture link we accept: https, no spaces, up to 400 characters. Anything else gives ''."""
    v = (value or "").strip()
    if not v or len(v) > 400 or re.search(r"\s", v):
        return ""
    if v.startswith("/api/v1/img/"):  # our own stored picture (set by the picture upload)
        return v
    return v if re.match(r"^https://[^/\s]+\.[^/\s]+", v, re.I) else ""


def _bad(out: CleanRows, i: int, name: str, why: str) -> None:
    out.invalid.append({"row": i, "name": name[:60], "why": why})


def check_rows(rows: list[dict]) -> CleanRows:
    """Check every row. Good rows come back shaped for ``ingest_items``; bad ones are explained."""
    out = CleanRows()
    seen: set[str] = set()
    for i, raw in enumerate(rows, 2):  # row 1 is the header (or the first line of a file without one)
        name = re.sub(r"\s+", " ", raw.get("name", "")).strip()
        if len(name) < 2:
            _bad(out, i, name, "the item name must be at least 2 characters")
            continue
        if len(name) > 200:
            name = name[:197].rsplit(" ", 1)[0] + "..."  # long supplier descriptions are shortened
        if re.search(r"https?:|www\.|@", name, re.I):
            _bad(out, i, name, "the item name must not contain a web address")
            continue
        price = _money(raw.get("price", ""))
        if price is None or not (MIN_PRICE <= price <= MAX_PRICE):
            _bad(out, i, name, f"the price must be a number from R{MIN_PRICE:.2f} to R{MAX_PRICE:,.0f}")
            continue
        promo = _money(raw.get("promo", "")) if raw.get("promo") else None
        if promo is not None and not (MIN_PRICE <= promo < price):
            promo = None  # a promo that is not lower than the price is ignored, not an error
        if not in_stock(raw.get("stock", "")):
            out.out_of_stock += 1
            continue
        try:
            category = store_category(raw.get("category", ""))
        except ValueError as exc:
            _bad(out, i, name, str(exc))
            continue
        if category not in CATEGORIES and category not in out.new_categories:
            out.new_categories.append(category)
        image = clean_image_url(raw.get("image", ""))
        if raw.get("image", "").strip():
            if image:
                out.pictures += 1
            else:
                out.bad_pictures += 1
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
                "brand": re.sub(r"\s+", " ", raw.get("brand", "")).strip()[:80],
                "category": category,
                "image_url": image,
                "ident": key,
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
    out_of_stock: int = 0
    new_categories: list[str] = field(default_factory=list)
    pictures: int = 0
    bad_pictures: int = 0

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
            "out_of_stock": self.out_of_stock,
            "new_categories": self.new_categories[:20],
            "pictures": self.pictures,
            "bad_pictures": self.bad_pictures,
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
        rows=len(rows),
        invalid=checked.invalid,
        duplicates=checked.duplicates,
        dry_run=dry_run,
        out_of_stock=checked.out_of_stock,
        new_categories=checked.new_categories,
        pictures=checked.pictures,
        bad_pictures=checked.bad_pictures,
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
