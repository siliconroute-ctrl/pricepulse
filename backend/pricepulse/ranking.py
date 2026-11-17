"""Which search hit is the best match for a search word?

A store's search for "brown mushrooms 250g" returns several products (other sizes, other brands,
mushroom soup...). The scraper keeps up to three and ranks them here: rank 1 is the closest to
what was asked for. Shoppers are shown only the rank 1 products (see visibility in queries.py);
the others stay in the database and the admin area can show them.
"""

from __future__ import annotations

import re
from typing import TypeVar

from rapidfuzz import fuzz

from .sizes import parse_size, split_size

T = TypeVar("T")


def _words(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text.lower())).strip()


def match_score(term: str, name: str, size_text: str = "") -> float:
    """0 to about 125: how closely a hit's name (and size) matches the search word."""
    term_base, term_size = split_size(term)
    name_base, name_size = split_size(name)
    a, b = _words(term_base or term), _words(name_base or name)
    # token_set alone calls "mushrooms" a perfect match for "mushroom soup 400g"; token_sort
    # punishes the extra words, so the plainer name wins.
    score = 0.5 * fuzz.token_set_ratio(a, b) + 0.5 * fuzz.token_sort_ratio(a, b)
    want = parse_size(term_size)
    have = parse_size(size_text or name_size)
    if want and have:
        score += 25 if want.key == have.key else -15
    return score


def rank_hits(
    term: str, hits: list[T], name=lambda h: h.name, size=lambda h: h.size
) -> list[T]:  # noqa: ANN001
    """The same hits, best match first. Ties keep the store's own order."""
    scored = [(-match_score(term, name(h), size(h) or ""), i, h) for i, h in enumerate(hits)]
    return [h for _, _, h in sorted(scored, key=lambda t: (t[0], t[1]))]
