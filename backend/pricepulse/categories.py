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


# Sub-categories (department) and the New / Refurbished label for the stores that sell them. This is
# one small list so the owner's full category database can replace it later.
SUBCATEGORIES: dict[str, list[str]] = {
    "Electronics": [
        "Laptops",
        "Desktops",
        "Screens",
        "Peripherals",
        "Mobile phones (Samsung / Android)",
        "iPhones",
    ],
}
CONDITIONS = ("New", "Refurbished")

_SUB_WORDS = {
    "laptop": "Laptops",
    "notebook": "Laptops",
    "desktop": "Desktops",
    "pc": "Desktops",
    "monitor": "Screens",
    "screen": "Screens",
    "display": "Screens",
    "peripheral": "Peripherals",
    "iphone": "iPhones",
    "ios": "iPhones",
    "android": "Mobile phones (Samsung / Android)",
    "samsung": "Mobile phones (Samsung / Android)",
    "phone": "Mobile phones (Samsung / Android)",
    "mobile": "Mobile phones (Samsung / Android)",
    **{
        w: "Peripherals"
        for w in (
            "mouse",
            "mice",
            "keyboard",
            "headset",
            "webcam",
            "speaker",
            "printer",
            "router",
            "usb",
            "cable",
            "charger",
            "dock",
            "hub",
            "adapter",
            "drive",
        )
    },
}


def clean_condition(text: str) -> str:
    """ "refurb", "Refurbished", "pre-owned" -> Refurbished; "new", "brand new" -> New; else ''."""
    t = re.sub(r"[^a-z]", "", (text or "").lower())
    if not t:
        return ""
    if t.startswith(("refurb", "preowned", "used", "secondhand", "reconditioned", "renewed")):
        return "Refurbished"
    if t in ("new", "brandnew", "newitem", "sealed"):
        return "New"
    return ""


def clean_subcategory(category: str, text: str) -> tuple[str, bool]:
    """Match what a store typed to our list for its category: (name, known). An unlisted value is kept
    as typed (``known`` False) so the preview can flag it; blank gives ('', True)."""
    t = re.sub(r"\s+", " ", (text or "").strip())[:60]
    if not t:
        return "", True
    names = SUBCATEGORIES.get(category)
    if not names:
        return t, True  # this category has no list: keep whatever the store typed
    for n in names:
        if n.lower() == t.lower():
            return n, True
    for w in re.findall(r"[a-z]+", t.lower()):
        hit = _SUB_WORDS.get(w) or _SUB_WORDS.get(w.rstrip("s"))
        if hit:
            return hit, True
    return t, False


# The categories a local store owner can choose at sign-up (one per store, fixed afterwards).
SIGNUP_CATEGORIES = ["Electronics", "Stationery", "Pets", "Health & Beauty", "Fresh", "Liquor", "Groceries"]
