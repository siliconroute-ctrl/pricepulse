"""Essential shopping items: the search words the scrapers look up every night.

The owner adds them from the admin area (one at a time from a request, or many from a CSV file).
The scraper machine fetches the list before each night run (see fetch_terms.py), so an item added
today is searched tonight and shows up with prices the next morning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import EssentialItem

CATEGORIES = ["Groceries", "Fresh", "Health & Beauty", "Liquor"]
MAX_ROWS = 500
_URLISH = re.compile(r"https?:|www\.|@|\.(com|co\.za|net|org)\b", re.I)
_OK = re.compile(r"[\w .,'&/%+()-]+")


def clean_term(text: str) -> str:
    """A search word list such as "white mushrooms 250g": 2 to 80 characters, no web addresses."""
    term = re.sub(r"\s+", " ", (text or "").strip())
    if len(term) < 2 or len(term) > 80:
        raise ValueError("needs 2 to 80 characters")
    if _URLISH.search(term) or not _OK.fullmatch(term):
        raise ValueError("only the item name, please (no links or odd symbols)")
    return term


def clean_category(text: str) -> str:
    wanted = (text or "").strip().lower()
    if not wanted:
        return "Groceries"
    for c in CATEGORIES:
        if c.lower() == wanted or c.lower().replace(" & ", " and ") == wanted:
            return c
    raise ValueError("category must be one of " + ", ".join(CATEGORIES))


@dataclass
class ImportResult:
    added: int = 0
    existing: int = 0
    invalid: list[dict] = field(default_factory=list)


def add_terms(s: Session, rows: list[dict], source: str) -> ImportResult:
    """Add rows ({"term", "category"}), skipping ones already there. Bad rows are reported, not fatal."""
    result = ImportResult()
    seen = {k for (k,) in s.execute(select(EssentialItem.key)).all()}
    for i, row in enumerate(rows[:MAX_ROWS], 1):
        raw = str((row or {}).get("term", ""))
        try:
            term = clean_term(raw)
            category = clean_category(str((row or {}).get("category", "")))
        except ValueError as exc:
            result.invalid.append({"row": i, "term": raw[:80], "why": str(exc)})
            continue
        key = term.lower()
        if key in seen:
            result.existing += 1
            continue
        seen.add(key)
        s.add(EssentialItem(term=term, key=key, category=category, source=source))
        result.added += 1
    return result
