"""South African ID numbers: validity, age, and a one-way fingerprint.

An ID number is YYMMDD SSSS C A Z: date of birth, a sequence number, a citizenship
digit (0 citizen, 1 permanent resident), a legacy digit and a check digit (Luhn).

The number itself is never stored or logged. We keep only:
- whether the person is 18 or older (and, for under-18s, the date they turn 18), and
- a keyed fingerprint (HMAC-SHA256 with a server secret), so one person cannot open
  several accounts. Without the secret the fingerprint cannot be turned back into,
  or guessed from, an ID number.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import date

ADULT_AGE = 18


@dataclass(frozen=True)
class IdInfo:
    """Result of checking an ID number. ``problem`` is plain language, or ""."""

    valid: bool
    problem: str = ""
    birth_date: date | None = None

    def age_on(self, today: date) -> int:
        b = self.birth_date
        return today.year - b.year - ((today.month, today.day) < (b.month, b.day))

    def adult_on(self) -> date:
        """The date this person turns 18 (29 Feb birthdays count from 1 March)."""
        b = self.birth_date
        try:
            return b.replace(year=b.year + ADULT_AGE)
        except ValueError:
            return date(b.year + ADULT_AGE, 3, 1)


def digits_only(text: str) -> str:
    """Remove spaces and dashes people type between groups."""
    return re.sub(r"[\s-]", "", str(text or ""))


def luhn_ok(number: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def check_id(id_number: str, today: date) -> IdInfo:
    """Validate a South African ID number and read the date of birth from it."""
    n = digits_only(id_number)
    if not n.isdigit() or len(n) != 13:
        return IdInfo(False, "A South African ID number has 13 digits. Please check and try again.")
    if n[10] not in "01":
        return IdInfo(False, "That does not look like a South African ID number. Please check the digits.")
    if not luhn_ok(n):
        return IdInfo(False, "That ID number is not valid. Please check each digit and try again.")
    yy, mm, dd = int(n[0:2]), int(n[2:4]), int(n[4:6])
    century = 2000 if yy <= today.year % 100 else 1900
    try:
        born = date(century + yy, mm, dd)
        if born > today:
            born = date(1900 + yy, mm, dd)
    except ValueError:
        return IdInfo(False, "The date of birth in that ID number is not a real date. Please check it.")
    return IdInfo(True, "", born)


def fingerprint(id_number: str, secret: str) -> str:
    """Keyed one-way fingerprint of an ID number. Needs the server secret."""
    if not secret or len(secret) < 16:
        raise ValueError("PP_ID_SECRET must be set to a long random value.")
    return hmac.new(secret.encode(), digits_only(id_number).encode(), hashlib.sha256).hexdigest()
