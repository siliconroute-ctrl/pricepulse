"""The 13-digit Store ID: 12 random digits plus a Luhn check digit, so a mistyped ID is caught."""

from __future__ import annotations

import secrets


def luhn_digit(body: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def make_store_id() -> str:
    body = "".join(secrets.choice("0123456789") for _ in range(12))
    if body[0] == "0":
        body = "1" + body[1:]
    return body + luhn_digit(body)


def valid_store_id(value: str) -> bool:
    v = str(value or "")
    return len(v) == 13 and v.isdigit() and luhn_digit(v[:12]) == v[12]
