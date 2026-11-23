"""Category corrections applied to what the stores' searches return.

A listing gets the category of the search word that found it ("watermelon" is searched under
Fresh), so a flavoured water found by that search would be filed as fresh produce. These small
rules fix such cases by the item's own name. Each rule only moves an item OUT of a wrong category.
"""

from __future__ import annotations

import re


def _words(name: str) -> set[str]:
    return set(re.findall(r"[a-z]+", (name or "").lower()))


def adjust_category(name: str, category: str) -> str:
    """Bottled water, sparkling water and flavoured water are shelf groceries, never fresh produce."""
    if category == "Fresh" and "water" in _words(name):
        return "Groceries"
    return category
