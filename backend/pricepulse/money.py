"""Money helpers. All money is South African rand, stored as integer cents."""

from __future__ import annotations

import re

_NUMBER = re.compile(r"\d[\d\s,]*(?:[.,]\d{1,2})?")


def clean_price(text: object) -> float | None:
    """Turn a price label into rand, or None when no price is present.

    Handles "R36.99", "R 1 249,99", "R1,249.99", "36.99", 36.99 and "R36\\n99"
    (cents in a separate element). Returns None for empty or non-positive input.
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text) if text > 0 else None
    s = str(text).replace("\xa0", " ").strip()
    # Some sites render cents in a separate element: "R36\n99" or "R 36 99".
    split_cents = re.fullmatch(r"R?\s*(\d[\d\s]*?)\s*[\n ]\s*(\d{2})", s)
    if split_cents and "." not in s and "," not in s:
        whole = split_cents.group(1).replace(" ", "")
        value = float(f"{whole}.{split_cents.group(2)}")
        return value if value > 0 else None
    m = _NUMBER.search(s)
    if not m:
        return None
    num = m.group(0).strip()
    # Decide which separator is decimal: the last "," or "." followed by 1-2 digits.
    dec = re.search(r"[.,](\d{1,2})$", num)
    if dec:
        whole = num[: dec.start()]
        cents = dec.group(1).ljust(2, "0")
    else:
        whole, cents = num, "00"
    whole = re.sub(r"[\s,.]", "", whole)
    if not whole:
        return None
    value = float(f"{whole}.{cents}")
    return value if value > 0 else None


def to_cents(rand: float | None) -> int | None:
    """Convert rand to integer cents, rounding half up."""
    if rand is None:
        return None
    return int(round(rand * 100 + 1e-9))


def from_cents(cents: int | None) -> float | None:
    """Convert integer cents back to rand."""
    return None if cents is None else cents / 100


def format_rand(rand: float) -> str:
    """Format like the UI: R 1 249.99 (space as thousands separator)."""
    whole, cents = f"{rand:.2f}".split(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return "R " + " ".join(groups) + "." + cents


def is_suspect(rand: float) -> bool:
    """Prices under R1 or over R5000 are probably parsing mistakes."""
    return rand < 1 or rand > 5000
