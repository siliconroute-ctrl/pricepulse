"""Match store listings to one canonical product.

Rules (from the requirements):
- Normalise brand, name, size and unit before comparing.
- Never match across different sizes.
- Never match across different brands, unless the item is a generic commodity
  (eggs, bananas, ...) that is flagged as such.
- Exact key match first; a fuzzy name match (rapidfuzz) above a threshold second.
- Anything in the grey zone goes to a review queue instead of being guessed.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .sizes import Size, parse_size, split_size

# Known South African grocery, liquor and pharmacy brands. Longest first is applied
# at lookup time so "Clover Krush" wins over "Clover" if both are listed.
KNOWN_BRANDS = [
    "Albany",
    "Sasko",
    "Blue Ribbon",
    "Sunbake",
    "Bakers Pride",
    "White Star",
    "Iwisa",
    "Ace",
    "Impala",
    "Pride",
    "Tastic",
    "Spekko",
    "Aunt Caroline",
    "Selati",
    "Huletts",
    "Illovo",
    "Five Roses",
    "Joko",
    "Freshpak",
    "Glen",
    "Ricoffy",
    "Frisco",
    "Nescafe",
    "Jacobs",
    "Sunfoil",
    "Excella",
    "Spar",
    "Lucky Star",
    "Glenryck",
    "Koo",
    "All Gold",
    "Rhodes",
    "Black Cat",
    "Jungle",
    "Bokomo",
    "Kelloggs",
    "Kellogg's",
    "Weet-Bix",
    "Future Life",
    "Maggi",
    "Knorr",
    "Royco",
    "Robertsons",
    "Rajah",
    "Mrs Balls",
    "Crosse & Blackwell",
    "Coca-Cola",
    "Coke",
    "Pepsi",
    "Sprite",
    "Fanta",
    "Stoney",
    "Appletiser",
    "Liqui-Fruit",
    "Ceres",
    "Oros",
    "Clover",
    "Parmalat",
    "Darling",
    "Douglasdale",
    "First Choice",
    "Fair Cape",
    "Lancewood",
    "Flora",
    "Rama",
    "Stork",
    "Danone",
    "Ultra Mel",
    "Yogi Sip",
    "Inkomazi",
    "Rainbow",
    "County Fair",
    "Farmer Brown",
    "Enterprise",
    "Eskort",
    "Bulls Eye",
    "Cadbury",
    "Beacon",
    "Simba",
    "Lays",
    "Doritos",
    "Willards",
    "Omo",
    "Ariel",
    "Surf",
    "Skip",
    "Maq",
    "Sunlight",
    "Handy Andy",
    "Domestos",
    "Jik",
    "Harpic",
    "Sta-Soft",
    "Comfort",
    "Twinsaver",
    "Baby Soft",
    "Huggies",
    "Pampers",
    "Pick n Pay",
    "No Name",
    "Ritebrand",
    "Checkers",
    "Simple Truth",
    "Woolworths",
    "Nan",
    "Nestle",
    "Lactogen",
    "Infacare",
    "S-26",
    "Purity",
    "Castle",
    "Castle Lite",
    "Black Label",
    "Carling Black Label",
    "Hansa",
    "Hansa Pilsener",
    "Windhoek",
    "Heineken",
    "Amstel",
    "Corona",
    "Stella Artois",
    "Savanna",
    "Hunters",
    "Bernini",
    "Brutal Fruit",
    "Flying Fish",
    "Klipdrift",
    "Klipdrift Premium",
    "Richelieu",
    "KWV",
    "Viceroy",
    "Oude Meester",
    "Jameson",
    "Bells",
    "Bell's",
    "J&B",
    "Johnnie Walker",
    "Jack Daniels",
    "Jack Daniel's",
    "Smirnoff",
    "Absolut",
    "Gordons",
    "Gordon's",
    "Inverroche",
    "Captain Morgan",
    "Red Heart",
    "Amarula",
    "Fat Bastard",
    "Nederburg",
    "Drostdy-Hof",
    "Robertson Winery",
    "Four Cousins",
    "Two Oceans",
    "Durbanville Hills",
    "Graca",
    "Tassenberg",
    "Autumn Harvest",
    "Panado",
    "Grand-Pa",
    "Disprin",
    "Med-Lemon",
    "Colgate",
    "Aquafresh",
    "Close-Up",
    "Sensodyne",
    "Aqueous Cream",
    "Vaseline",
    "Nivea",
    "Dove",
    "Shield",
    "Mum",
    "Lux",
    "Protex",
    "Lifebuoy",
    "Dettol",
    "Johnson's",
    "Always",
    "Lil-lets",
    "Kotex",
    "Sorbet",
    "Clere",
    "Head & Shoulders",
    "Pantene",
    "Sunsilk",
    "Gillette",
    "Bic",
    "Redoxon",
    "Centrum",
    "Vital",
    "Solal",
    "Dis-Chem",
    "Sanatogen",
    "Pharmaton",
]


def _brand_key(text: str) -> str:
    """Reduce a brand to lowercase words: "Coca-Cola" -> "coca cola"."""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9& ]+", " ", t.replace("'", ""))
    return re.sub(r"\s+", " ", t).strip()


_BRANDS_NORM = sorted({_brand_key(b) for b in KNOWN_BRANDS}, key=len, reverse=True)
# Brand spellings that mean the same brand (in _brand_key form).
BRAND_ALIASES = {
    "coke": "coca cola",
    "carling black label": "black label",
    "hansa pilsener": "hansa",
    "klipdrift premium": "klipdrift",
    "nestle nan": "nan",
}

# Words that sellers add but that do not change what the product is.
MARKETING_WORDS = {
    "fresh",
    "new",
    "value",
    "pack",
    "valuepack",
    "value-pack",
    "promo",
    "special",
    "deal",
    "save",
    "bonus",
    "free",
    "offer",
    "limited",
    "edition",
    "bottle",
    "can",
    "cans",
    "nrb",
    "rb",
    "pet",
    "tin",
    "jar",
    "box",
    "bag",
    "the",
    "and",
    "with",
    "of",
    "in",
    "a",
    "original",
    "classic",
    "premium",
    "brand",
    "approx",
    "approximately",
    "sa",
    "south",
    "african",
    "product",
    "range",
    "everyday",
    "pure",
    "loose",
    "prepacked",
    "pre",
    "packed",
    "bottles",
}
# Generic commodities can match across brands (store brand eggs vs loose eggs).
GENERIC_WORDS = {
    "eggs",
    "egg",
    "bananas",
    "banana",
    "apples",
    "apple",
    "potatoes",
    "potato",
    "onions",
    "onion",
    "tomatoes",
    "tomato",
    "carrots",
    "carrot",
    "oranges",
    "orange",
    "lemons",
    "lemon",
    "avocados",
    "avocado",
    "cabbage",
    "lettuce",
    "butternut",
    "pumpkin",
    "spinach",
    "cucumber",
    "peppers",
    "mince",
    "chicken",
    "pork",
    "beef",
    "lamb",
    "boerewors",
}
GENERIC_STOP = {"large", "medium", "small", "extra", "fresh"}
# Extra words that describe the product type without changing it. "Castle Lite" and
# "Castle Lite Beer" are the same; "Coca-Cola" and "Coca-Cola No Sugar" are not.
DESCRIPTOR_WORDS = {
    "beer",
    "lager",
    "cider",
    "wine",
    "brandy",
    "whisky",
    "whiskey",
    "gin",
    "vodka",
    "rum",
    "liqueur",
    "paracetamol",
    "instant",
    "liquid",
    "deodorant",
    "toothpaste",
    "tea",
    "drink",
    "beverage",
    "soft",
    "cola",
    "spirit",
    "aperitif",
    "tablet",
}
WINE_COLOURS = {"white", "red", "rose"}

DEFAULT_AUTO_THRESHOLD = 88.0
DEFAULT_REVIEW_THRESHOLD = 70.0


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _norm_text(text: str) -> str:
    t = _ascii(text).lower().replace("&", " & ")
    t = re.sub(r"[^a-z0-9&' ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def detect_brand(name: str, brand_hint: str | None = None) -> str:
    """Return the normalised brand, or "" when unknown."""
    if brand_hint:
        b = _brand_key(brand_hint)
        return BRAND_ALIASES.get(b, b)
    t = " " + _brand_key(name) + " "
    # Most names start with the brand. Otherwise accept a longer brand anywhere.
    for norm in list(BRAND_ALIASES) + _BRANDS_NORM:
        if t.startswith(f" {norm} "):
            return BRAND_ALIASES.get(norm, norm)
    for norm in _BRANDS_NORM:
        if len(norm) >= 6 and f" {norm} " in t:
            return BRAND_ALIASES.get(norm, norm)
    return ""


def _singular(word: str) -> str:
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def normalise_name(name: str, brand: str = "") -> str:
    """Lowercase, drop size text, brand and marketing words; sort tokens."""
    base, _size = split_size(name)
    t = " " + _brand_key(base) + " "
    if brand:
        names = [brand] + [alias for alias, target in BRAND_ALIASES.items() if target == brand]
        for n in sorted(names, key=len, reverse=True):
            t = t.replace(f" {n} ", " ", 1)
    t = re.sub(r"\bteabags?\b", "tea bag", t)
    t = re.sub(r"\broll on\b", "rollon", t)
    tokens = [_singular(w) for w in t.split() if w != "&"]
    tokens = [w for w in tokens if w not in MARKETING_WORDS]
    return " ".join(sorted(set(tokens)))


def is_generic(norm_name: str) -> bool:
    """True when the item is a plain commodity that may match across brands."""
    words = [w for w in norm_name.split() if w not in GENERIC_STOP]
    if not words:
        return False
    singular = {w.rstrip("s") for w in GENERIC_WORDS}
    return all(w in GENERIC_WORDS or w.rstrip("s") in singular for w in words) and len(words) <= 3


@dataclass
class Normalised:
    """A listing reduced to the fields used for matching."""

    brand: str
    name: str
    size: Size | None
    generic: bool

    @property
    def size_key(self) -> str:
        return self.size.key if self.size else ""

    @property
    def key(self) -> str:
        brand = "" if self.generic else self.brand
        return f"{brand}|{self.name}|{self.size_key}"


def normalise(name: str, size_text: str | None = None, brand_hint: str | None = None) -> Normalised:
    """Normalise a raw listing. Size comes from ``size_text`` or, failing that, the name."""
    if not size_text:
        _, size_text = split_size(name)
    size = parse_size(size_text)
    brand = detect_brand(name, brand_hint)
    norm = normalise_name(name, brand)
    return Normalised(brand=brand, name=norm, size=size, generic=is_generic(norm))


@dataclass
class MatchResult:
    """Outcome of matching one listing against known products."""

    product_id: int | None
    score: float
    status: str  # "exact", "fuzzy", "needs_review", "new"
    reason: str = ""
    suggestion_id: int | None = None


@dataclass
class Candidate:
    """A canonical product the matcher can choose from."""

    id: int
    norm: Normalised
    stores: set[str] = field(default_factory=set)


def compatible(a: Normalised, b: Normalised) -> bool:
    """Hard rules: same size, and same brand unless both are generic commodities."""
    if not a.size or not b.size or a.size_key != b.size_key:
        return False
    if a.generic and b.generic:
        return True
    return a.brand == b.brand


def _numbers(name: str) -> set[str]:
    return {w for w in name.split() if w.isdigit()}


def score(a: Normalised, b: Normalised) -> float:
    """Name similarity 0-100, only meaningful when ``compatible`` is True.

    Numbers in the name must agree (nappy size 4 vs 5, formula stage 1 vs 2).
    When one name is the other plus harmless type words ("beer"), it scores 98.
    """
    if a.name == b.name:
        return 100.0
    if _numbers(a.name) != _numbers(b.name):
        return 0.0
    ta, tb = set(a.name.split()), set(b.name.split())
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if small <= big:
        extra = big - small
        allowed = DESCRIPTOR_WORDS | (WINE_COLOURS if "wine" in extra else set())
        if extra <= allowed:
            return 98.0
    return float(fuzz.token_sort_ratio(a.name, b.name))


def match(
    item: Normalised,
    candidates: list[Candidate],
    store: str | None = None,
    auto_threshold: float = DEFAULT_AUTO_THRESHOLD,
    review_threshold: float = DEFAULT_REVIEW_THRESHOLD,
) -> MatchResult:
    """Pick the canonical product for ``item`` or say it is new / needs review."""
    if item.size is None:
        return MatchResult(None, 0.0, "needs_review", "size could not be read")
    best: tuple[float, Candidate] | None = None
    for c in candidates:
        if not compatible(item, c.norm):
            continue
        # A store never sells two listings of one product; skip products it already has.
        if store and store in c.stores and item.key != c.norm.key:
            continue
        s = score(item, c.norm)
        if best is None or s > best[0]:
            best = (s, c)
    if best is None:
        return MatchResult(None, 0.0, "new", "no product with same brand and size")
    s, c = best
    if item.key == c.norm.key:
        return MatchResult(c.id, 100.0, "exact")
    if not item.brand and not item.generic:
        # Unknown brand: only an identical name is safe; anything else is a guess.
        if s >= review_threshold:
            return MatchResult(None, s, "needs_review", "brand unknown, similar name", suggestion_id=c.id)
        return MatchResult(None, s, "new", "brand unknown")
    if s >= auto_threshold:
        return MatchResult(c.id, s, "fuzzy")
    if s >= review_threshold:
        return MatchResult(None, s, "needs_review", "similar name, not sure", suggestion_id=c.id)
    return MatchResult(None, s, "new", "no close name match")


def same_product(a: tuple[str, str], b: tuple[str, str], threshold: float = DEFAULT_AUTO_THRESHOLD) -> bool:
    """Convenience check used by tests: do two (name, size) listings match?"""
    na, nb = normalise(*a), normalise(*b)
    if not compatible(na, nb):
        return False
    if na.key == nb.key:
        return True
    return bool(na.brand or na.generic) and score(na, nb) >= threshold
