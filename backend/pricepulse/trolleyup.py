"""Export a PricePulse session as a file TrolleyUp can import.

TrolleyUp (trolleyup.com) has "Import items from a CSV file" on a list's Items tab.
It reads columns named Item, Quantity, Store and Note, and applies its own rules:
item names up to 40 characters and 7 words, quantity 1 to 99, notes up to 80
characters, no links or long digit runs, up to 100 rows. We write to those rules.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

NAME_MAX, NAME_WORDS, NOTE_MAX, ROWS_MAX = 40, 7, 80, 100
# TrolleyUp's own names for the liquor sections of grocery chains.
LIQUOR_STORES = {"Checkers": "Checkers LiquorShop", "Pick n Pay": "Pick n Pay Liquor"}


@dataclass
class ExportItem:
    name: str
    size: str
    category: str
    quantity: int
    store: str  # where to buy it; "" = any store
    price: float | None


def short_name(name: str, size: str) -> str:
    """Fit TrolleyUp's item-name rules, keeping the size at the end when there is room."""

    def clean(t: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"https?://\S+|www\.\S+|\d{9,}", "", t or "")).strip()

    name = clean(name)
    size = re.sub(r"\s*[x×]\s*", "x", clean(size))  # "24 x 340ml" -> "24x340ml"
    if size and size.lower().replace(" ", "") in name.lower().replace(" ", ""):
        size = ""
    tail = size.split()
    words = name.split()[: max(1, NAME_WORDS - len(tail))]
    while len(words) > 1 and len(" ".join(words + tail)) > NAME_MAX:
        words.pop()
    return " ".join(words + tail)[:NAME_MAX].strip()


def store_name(store: str, category: str) -> str:
    return LIQUOR_STORES.get(store, store) if category == "Liquor" else store


def _guard(cell: str) -> str:
    """Stop spreadsheets treating a cell as a formula (TrolleyUp strips the mark again)."""
    return "'" + cell if cell and cell[0] in "=+-@" else cell


def to_csv(items: list[ExportItem], date_label: str) -> str:
    """CSV text with a BOM, ready for TrolleyUp's import (and for Excel)."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["Item", "Quantity", "Store", "Note"])
    for it in items[:ROWS_MAX]:
        note = f"PricePulse price R {it.price:.2f} on {date_label}" if it.price is not None else ""
        w.writerow(
            [
                _guard(short_name(it.name, it.size)),
                max(1, min(99, int(it.quantity))),
                _guard(store_name(it.store, it.category)),
                note[:NOTE_MAX],
            ]
        )
    return "﻿" + buf.getvalue()
