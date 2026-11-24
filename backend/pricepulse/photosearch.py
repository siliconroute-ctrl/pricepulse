"""Photo search: the phone reads a barcode or the label words from a photo (the photo itself never leaves
the phone); this module checks what it read against our products and keeps to the grocery section.

Allowed: Groceries and Fresh, and Liquor for adults. Anything else a photo turns out to be is refused.
"""

from __future__ import annotations

import re

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .accounts import LIQUOR, RuleError, is_tester, sa_today
from .db import AppUser, Listing, PhotoUse, Price, Product, Store
from .queries import Scope, scope_rows, store_live, visible_clause

DAILY_LIMIT = 20  # photo searches per account per day (testers unlimited)
GROCERY_CATEGORIES = ("Groceries", "Fresh")
MAX_RESULTS = 5
STOP = {
    "the", "and", "with", "for", "from", "best", "before", "use", "net", "per", "serve", "serving",
    "ingredients", "contains", "may", "store", "keep", "cool", "dry", "place", "produced", "packed",
    "product", "south", "africa", "made", "www", "com", "tel", "ltd", "pty", "each", "new", "free",
}  # fmt: skip


def code_variants(code: str) -> list[str]:
    """A scanned number as it may be stored: UPC-A (12 digits) and EAN-13 differ by a leading zero."""
    digits = re.sub(r"\D", "", code or "")
    if not 6 <= len(digits) <= 14:
        return []
    out = {digits, digits.lstrip("0")}
    if len(digits) == 12:
        out.add("0" + digits)
    if len(digits) == 13 and digits.startswith("0"):
        out.add(digits[1:])
    return sorted(x for x in out if x)


def words_from(text: str) -> list[str]:
    """The useful words read from a label: letters and digits, at least three characters, no filler."""
    seen: list[str] = []
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if len(w) >= 3 and w not in STOP and not w.isdigit() and w not in seen:
            seen.append(w)
    return seen[:12]


def check_daily(s: Session, user: AppUser) -> int:
    """Count one photo search for today; refuse past the daily limit. Returns how many are left."""
    if is_tester(user):
        return 9999
    day = sa_today().isoformat()
    row = s.scalar(select(PhotoUse).where(PhotoUse.uid == user.uid, PhotoUse.day == day))
    if row is None:
        row = PhotoUse(uid=user.uid, day=day, count=0)
        s.add(row)
    if row.count >= DAILY_LIMIT:
        raise RuleError(
            "photo_limit",
            f"You have used your {DAILY_LIMIT} photo searches for today. You can still type your search.",
            429,
        )
    row.count += 1
    s.flush()
    return DAILY_LIMIT - row.count


def _candidates(s: Session, region: Scope | str, where) -> list[Product]:  # noqa: ANN001
    """Products with a live listing (a price in this shopper's area) that shoppers may see."""
    stmt = (
        select(Product)
        .join(Listing, Listing.product_id == Product.id)
        .join(Store, Store.id == Listing.store_id)
        .join(Price, Price.listing_id == Listing.id)
        .where(where, Listing.active.is_(True), store_live(), scope_rows(region), visible_clause())
        .distinct()
        .limit(60)
    )
    return list(s.scalars(stmt))


def lookup(
    s: Session, region: Scope | str, adult: bool, code: str = "", text: str = ""
) -> tuple[str, str, list[Product]]:
    """Returns (status, message, products). status: found, refused, not_found or unreadable."""
    prods: list[Product] = []
    what = ""
    variants = code_variants(code)
    if variants:
        what = "barcode " + re.sub(r"\D", "", code)
        prods = _candidates(s, region, Listing.barcode.in_(variants))
    elif text.strip():
        words = words_from(text)
        if len(words) < 2:
            return "unreadable", "I could not read enough of the label. Try a clearer photo of the front.", []
        what = "the label"
        long_first = sorted(words, key=len, reverse=True)[:6]
        found = _candidates(
            s,
            region,
            or_(
                *[
                    or_(func.lower(Product.name).like(f"%{w}%"), func.lower(Product.brand).like(f"%{w}%"))
                    for w in long_first
                ]
            ),
        )
        scored = []
        for p in found:
            hay = f"{p.name} {p.brand} {p.size_text}".lower()
            hits = sum(1 for w in words if w in hay)
            if hits >= 2 and hits >= len(words) / 3:
                scored.append((hits, p))
        scored.sort(key=lambda x: (-x[0], x[1].name))
        prods = [p for _h, p in scored[:12]]
    else:
        return "unreadable", "I could not find a barcode or readable label words in the photo.", []
    if not prods:
        return (
            "not_found",
            f"I read {what}, but PricePulse has no matching product yet. You can type the name.",
            [],
        )
    allowed = [p for p in prods if p.category in GROCERY_CATEGORIES or (p.category == LIQUOR and adult)]
    if not allowed:
        liquor = any(p.category == LIQUOR for p in prods)
        if liquor:
            return "refused", "Liquor is only for people aged 18 and over.", []
        cat = prods[0].category or "another section"
        return (
            "refused",
            f"That is not a grocery item (it is in {cat}). Photo search is only for groceries, fresh food "
            "and liquor.",
            [],
        )
    return "found", f"I found {what}.", allowed[:MAX_RESULTS]
