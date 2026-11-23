"""South African phone numbers for the Call and WhatsApp buttons."""

from __future__ import annotations

import re


def normalise_phone(text: str) -> str:
    """ "082 123 4567", "+27 82 123 4567", "(021) 555-1234" -> "27821234567" / "27215551234".
    Anything that is not a South African number gives "" ."""
    digits = re.sub(r"\D", "", text or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 10 and digits.startswith("0"):
        digits = "27" + digits[1:]
    if len(digits) == 11 and digits.startswith("27") and digits[2] in "12345678":
        return digits
    return ""


def pretty_phone(digits: str) -> str:
    """ "27821234567" -> "082 123 4567" (what a South African expects to read)."""
    d = digits if digits.startswith("27") and len(digits) == 11 else ""
    if not d:
        return digits
    n = "0" + d[2:]
    return f"{n[:3]} {n[3:6]} {n[6:]}"
