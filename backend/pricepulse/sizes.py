"""Parse pack sizes like "2L", "24 x 340ml", "18 pack" or "per kg" into base units.

Base units are grams (g), millilitres (ml) and items (each). A pack multiplier is
kept separately so "6 x 330ml" and "1.98L" are not treated as the same product.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_UNIT = {
    "kg": ("g", 1000.0),
    "kgs": ("g", 1000.0),
    "g": ("g", 1.0),
    "gr": ("g", 1.0),
    "gram": ("g", 1.0),
    "grams": ("g", 1.0),
    "mg": ("g", 0.001),
    "l": ("ml", 1000.0),
    "lt": ("ml", 1000.0),
    "ltr": ("ml", 1000.0),
    "litre": ("ml", 1000.0),
    "litres": ("ml", 1000.0),
    "liter": ("ml", 1000.0),
    "liters": ("ml", 1000.0),
    "ml": ("ml", 1.0),
    "cl": ("ml", 10.0),
}
_COUNT_WORDS = (
    r"pack|pk|packs|tablets?|tabs?|capsules?|caps|sachets?|rolls?|bags?|teabags?|ea|each|"
    r"nappies|diapers|pieces|pcs|pc|units?|'s|s|eggs|wipes|count|ct"
)
_NUM = r"\d+(?:[.,]\d+)?"
_UNITS_RE = "|".join(sorted(_UNIT, key=len, reverse=True))

_PACK_RE = re.compile(rf"(?P<n>\d+)\s*[x×*]\s*(?P<q>{_NUM})\s*(?P<u>{_UNITS_RE})\b", re.I)
_PACK_REV_RE = re.compile(rf"(?P<q>{_NUM})\s*(?P<u>{_UNITS_RE})\s*[x×*]\s*(?P<n>\d+)\b", re.I)
_AMOUNT_RE = re.compile(rf"(?P<q>{_NUM})\s*(?P<u>{_UNITS_RE})\b", re.I)
_COUNT_RE = re.compile(rf"(?P<n>\d+)\s*(?P<w>{_COUNT_WORDS})(?![a-z])", re.I)
_PER_KG_RE = re.compile(r"(?:\bper\s*kg\b|/\s*kg\b|\bloose\b|\bby weight\b)", re.I)

# Anything the size parser understands, used to cut a size out of a product name.
SIZE_IN_NAME_RE = re.compile(
    rf"(?:\d+\s*[x×*]\s*{_NUM}\s*(?:{_UNITS_RE})\b"
    rf"|{_NUM}\s*(?:{_UNITS_RE})\s*[x×*]\s*\d+\b"
    rf"|{_NUM}\s*(?:{_UNITS_RE})\b(?!\s*[x×*])"
    rf"|\b\d+\s*(?:{_COUNT_WORDS})(?![a-z])"
    rf"|\bper\s*kg\b)",
    re.I,
)


def _num(text: str) -> float:
    return float(text.replace(",", "."))


@dataclass(frozen=True)
class Size:
    """A parsed size. ``qty`` is per item in ``unit``; ``pack`` is how many items."""

    qty: float
    unit: str  # "g", "ml" or "each"
    pack: int = 1
    by_weight: bool = False  # sold loose, priced per kg

    @property
    def total(self) -> float:
        return self.qty * self.pack

    @property
    def key(self) -> str:
        """A stable key used for matching. Equal keys mean the same size."""
        if self.by_weight:
            return "per-kg"
        qty = round(self.qty, 2)
        qty_txt = f"{qty:g}"
        return f"{self.pack}x{qty_txt}{self.unit}"

    def unit_price(self, price: float) -> tuple[float, str] | None:
        """Price per 100g, per 100ml or per item, so different packs compare fairly."""
        if price is None or price <= 0:
            return None
        if self.by_weight:
            return round(price / 10, 2), "per 100g"
        if self.total <= 0:
            return None
        if self.unit == "each":
            return round(price / self.total, 2), "per item"
        label = "per 100g" if self.unit == "g" else "per 100ml"
        return round(price / self.total * 100, 2), label


def parse_size(text: str | None) -> Size | None:
    """Parse a size label. Returns None when nothing size-like is found.

    >>> parse_size("24 x 340ml").key
    '24x340ml'
    >>> parse_size("2ltr").key
    '1x2000ml'
    """
    if not text:
        return None
    s = str(text).strip().lower().replace("×", "x")
    if not s:
        return None
    if _PER_KG_RE.search(s) or s in {"kg", "loose"}:
        return Size(qty=1000.0, unit="g", by_weight=True)
    if s in {"each", "ea", "single", "1 each"}:
        return Size(qty=1.0, unit="each")

    m = _PACK_RE.search(s) or _PACK_REV_RE.search(s)
    if m:
        unit, factor = _UNIT[m.group("u").lower()]
        return Size(qty=_num(m.group("q")) * factor, unit=unit, pack=int(m.group("n")))

    count = _COUNT_RE.search(s)
    amount = _AMOUNT_RE.search(s)
    # "500mg" next to a count is a strength, not a size: "24 tablets 500mg".
    if amount and amount.group("u").lower() == "mg" and count:
        amount = None
    if amount and count:
        # "6 pack 330ml" style: count acts as the multiplier.
        unit, factor = _UNIT[amount.group("u").lower()]
        return Size(qty=_num(amount.group("q")) * factor, unit=unit, pack=int(count.group("n")))
    if amount:
        unit, factor = _UNIT[amount.group("u").lower()]
        return Size(qty=_num(amount.group("q")) * factor, unit=unit)
    if count:
        n = int(count.group("n"))
        if n > 0:
            return Size(qty=float(n), unit="each")
    return None


def split_size(name: str) -> tuple[str, str]:
    """Split "Clover Full Cream Milk 2L" into ("Clover Full Cream Milk", "2L").

    Uses the last size-like phrase in the name. Returns (name, "") when none.
    """
    if not name:
        return "", ""
    matches = list(SIZE_IN_NAME_RE.finditer(name))
    if not matches:
        return name.strip(), ""
    m = matches[-1]
    size = m.group(0).strip()
    rest = (name[: m.start()] + " " + name[m.end() :]).strip()
    rest = re.sub(r"\s{2,}", " ", rest).strip(" -,|()")
    return rest, size
