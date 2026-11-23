"""New-item searches: decide whether a word a paid shopper typed belongs to a category we cover,
and queue a search for the scraper machine.

Plain rules only (no outside service): the word is cleaned, checked against short word lists and
given one of our categories. Prices are never guessed: the scraper machine reads the stores' own
pages and uploads what it finds, exactly like the night run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .accounts import RuleError, clean_request, is_adult, is_paid
from .db import AppUser, SearchJob, utcnow

DAILY_LIMIT = 5  # searches one person may start per day
REUSE_DAYS = 7  # the same word asked again within this time reuses the earlier search
STALE_RUNNING = timedelta(minutes=30)  # a job that never reported back goes back in the queue
NEW_DAYS = 14  # how long a found product carries the "New" tag

COVERED = "We only search for groceries, fresh food, liquor, health and beauty items and pet food."

# Words that are never searched: other kinds of goods and things we will not list.
BLOCKED = {
    # not our categories
    "laptop",
    "laptops",
    "computer",
    "computers",
    "phone",
    "phones",
    "cellphone",
    "tablet pc",
    "tv",
    "television",
    "monitor",
    "monitors",
    "printer",
    "printers",
    "router",
    "routers",
    "camera",
    "cameras",
    "headphones",
    "earphones",
    "speaker",
    "speakers",
    "charger",
    "cable",
    "cables",
    "battery",
    "batteries",
    "playstation",
    "xbox",
    "console",
    "furniture",
    "sofa",
    "mattress",
    "bed",
    "shoes",
    "sneakers",
    "jeans",
    "shirt",
    "dress",
    "jacket",
    "tyre",
    "tyres",
    "drill",
    "hammer",
    "paint",
    "ink",
    "toner",
    "pen",
    "pens",
    "pencil",
    "pencils",
    "stationery",
    "notebook",
    "notebooks",
    # things we do not list at all
    "gun",
    "guns",
    "firearm",
    "firearms",
    "rifle",
    "pistol",
    "ammunition",
    "ammo",
    "knife",
    "knives",
    "explosive",
    "explosives",
    "dagga",
    "cannabis",
    "marijuana",
    "weed",
    "cocaine",
    "heroin",
    "meth",
    "mandrax",
    "tik",
    "ecstasy",
    "vape",
    "vapes",
    "cigarette",
    "cigarettes",
    "tobacco",
    "cigar",
    "cigars",
    "porn",
    "sex",
    "viagra",
    "steroid",
    "steroids",
}

LIQUOR_WORDS = {
    "wine",
    "beer",
    "lager",
    "ale",
    "stout",
    "cider",
    "whisky",
    "whiskey",
    "vodka",
    "gin",
    "rum",
    "brandy",
    "tequila",
    "liqueur",
    "champagne",
    "sparkling",
    "cabernet",
    "merlot",
    "shiraz",
    "chardonnay",
    "sauvignon",
    "pinotage",
    "chenin",
    "rose",
    "port",
    "sherry",
    "cooler",
    "castle",
    "heineken",
    "savanna",
    "hunters",
    "smirnoff",
    "klipdrift",
    "johnnie",
    "jameson",
    "amarula",
    "brutal",
    "fruit",
}
LIQUOR_STRONG = LIQUOR_WORDS - {"port", "rose", "fruit", "ale", "cooler"}

PET_WORDS = {
    "dog",
    "dogs",
    "puppy",
    "puppies",
    "cat",
    "cats",
    "kitten",
    "kittens",
    "pet",
    "pets",
    "bird",
    "budgie",
    "parrot",
    "hamster",
    "rabbit",
    "fish",
    "aquarium",
    "litter",
    "kibble",
    "pedigree",
    "whiskas",
    "bakers",
    "hills",
    "royal",
    "canin",
    "purina",
    "friskies",
    "eukanuba",
    "chappie",
    "catmor",
    "doggy",
    "biltong",
}
PET_STRONG = {
    "dog",
    "dogs",
    "puppy",
    "puppies",
    "cat",
    "cats",
    "kitten",
    "kittens",
    "pet",
    "pets",
    "kibble",
    "litter",
    "pedigree",
    "whiskas",
    "friskies",
    "eukanuba",
    "chappie",
    "catmor",
    "budgie",
    "hamster",
}

FRESH_WORDS = {
    "apple",
    "apples",
    "banana",
    "bananas",
    "orange",
    "oranges",
    "lemon",
    "lemons",
    "grape",
    "grapes",
    "pear",
    "pears",
    "peach",
    "peaches",
    "plum",
    "plums",
    "mango",
    "mangoes",
    "pineapple",
    "melon",
    "watermelon",
    "strawberries",
    "strawberry",
    "blueberries",
    "avocado",
    "avocados",
    "tomato",
    "tomatoes",
    "potato",
    "potatoes",
    "onion",
    "onions",
    "carrot",
    "carrots",
    "cabbage",
    "lettuce",
    "spinach",
    "cucumber",
    "pepper",
    "peppers",
    "broccoli",
    "cauliflower",
    "beetroot",
    "garlic",
    "ginger",
    "celery",
    "mushroom",
    "mushrooms",
    "pumpkin",
    "butternut",
    "sweetcorn",
    "beans",
    "peas",
    "herbs",
    "basil",
    "parsley",
    "coriander",
    "mince",
    "chicken",
    "pork",
    "beef",
    "lamb",
    "steak",
    "sausage",
    "boerewors",
    "bacon",
    "ham",
    "fish",
    "hake",
    "salmon",
    "prawns",
    "eggs",
    "egg",
    "milk",
    "yoghurt",
    "yogurt",
    "cheese",
    "butter",
    "cream",
    "bread",
    "rolls",
    "croissant",
    "salad",
    "fruit",
    "vegetables",
    "veg",
}
HEALTH_WORDS = {
    "shampoo",
    "conditioner",
    "soap",
    "toothpaste",
    "toothbrush",
    "deodorant",
    "lotion",
    "moisturiser",
    "moisturizer",
    "sunscreen",
    "razor",
    "razors",
    "tampons",
    "pads",
    "nappies",
    "nappy",
    "wipes",
    "vitamin",
    "vitamins",
    "tablets",
    "capsules",
    "syrup",
    "plasters",
    "plaster",
    "bandage",
    "panado",
    "paracetamol",
    "ibuprofen",
    "cough",
    "lozenges",
    "perfume",
    "cologne",
    "makeup",
    "lipstick",
    "mascara",
    "foundation",
    "nail",
    "cream",
    "ointment",
    "supplement",
    "supplements",
    "protein",
    "mouthwash",
    "floss",
    "tissues",
    "cotton",
    "baby",
    "formula",
}
# Tinned, packed and cooked foods are shelf groceries even when they name a fresh ingredient.
PANTRY_WORDS = {
    "baked",
    "sauce",
    "tinned",
    "canned",
    "tin",
    "jam",
    "pickled",
    "dried",
    "frozen",
    "spread",
    "soup",
    "stew",
    "chutney",
    "paste",
    "puree",
    "juice",
    "cereal",
    "rice",
    "pasta",
    "flour",
    "sugar",
    "oil",
    "vinegar",
    "mayonnaise",
    "chips",
    "crisps",
    "biscuits",
    "rusks",
    "powder",
    "mix",
    "stock",
    "noodles",
}
FRESH_STRONG = FRESH_WORDS - {"cream", "fish", "fruit", "butter", "eggs", "egg", "milk", "cheese", "bread"}
HEALTH_STRONG = HEALTH_WORDS - {"cream", "baby", "formula", "protein", "cotton", "nail", "pads"}


@dataclass
class Verdict:
    ok: bool
    category: str = ""
    term: str = ""
    reason: str = ""


def _words(term: str) -> list[str]:
    return re.findall(r"[a-z]+", term.lower())


def classify(term: str, adult: bool = True) -> Verdict:
    """Is this word one we search for, and in which category? Order matters: pets and liquor first,
    because "dog biltong" is pet food and "wine" is not fresh produce."""
    try:
        cleaned = clean_request(term)
    except RuleError as exc:
        return Verdict(False, reason=exc.message)
    words = _words(cleaned)
    letters = [w for w in words if len(w) >= 3]
    if not letters:
        return Verdict(False, reason="Please type the name of the item, for example: dog food 8kg.")
    joined = " ".join(words)
    for w in words:
        if w in BLOCKED or joined in BLOCKED:
            return Verdict(False, reason=COVERED)
    ws = set(words)
    if ws & PET_STRONG or (ws & PET_WORDS and ws & {"food", "treats", "treat", "chews", "biscuits"}):
        return Verdict(True, "Pets", cleaned)
    if ws & LIQUOR_STRONG:
        if not adult:
            return Verdict(False, reason="Liquor searches are only for people aged 18 and over.")
        return Verdict(True, "Liquor", cleaned)
    if ws & HEALTH_STRONG:
        return Verdict(True, "Health & Beauty", cleaned)
    if ws & PANTRY_WORDS:
        return Verdict(True, "Groceries", cleaned)
    if ws & FRESH_STRONG:
        return Verdict(True, "Fresh", cleaned)
    if ws & LIQUOR_WORDS:
        return Verdict(True, "Liquor", cleaned) if adult else Verdict(False, reason=COVERED)
    if ws & HEALTH_WORDS:
        return Verdict(True, "Health & Beauty", cleaned)
    if ws & FRESH_WORDS:
        return Verdict(True, "Fresh", cleaned)
    return Verdict(True, "Groceries", cleaned)


def start_search(s: Session, user: AppUser, term: str, today=None) -> tuple[SearchJob, bool]:
    """Queue a search for a paid or tester account. Returns (job, created).

    The same word asked again within a week reuses the earlier search and does not count against
    the person's daily limit."""
    if not is_paid(s, user):
        raise RuleError(
            "paid_only",
            "Searching for new items is part of the paid plan. You can still request an item.",
            403,
        )
    verdict = classify(term, adult=is_adult(user, utcnow().date()))
    if not verdict.ok:
        raise RuleError("not_covered", verdict.reason, 422)
    key = verdict.term.lower()
    now = utcnow()
    recent = s.scalar(
        select(SearchJob)
        .where(SearchJob.key == key, SearchJob.created_at > now - timedelta(days=REUSE_DAYS))
        .order_by(SearchJob.id.desc())
    )
    if recent is not None and recent.status != "failed":
        return recent, False
    today_count = s.scalar(
        select(func.count())
        .select_from(SearchJob)
        .where(SearchJob.uid == user.uid, SearchJob.created_at > now - timedelta(hours=24))
    )
    if (today_count or 0) >= DAILY_LIMIT:
        raise RuleError(
            "daily_limit",
            f"You have used your {DAILY_LIMIT} new-item searches for today. Try again tomorrow.",
            429,
        )
    job = SearchJob(uid=user.uid, term=verdict.term, key=key, category=verdict.category)
    s.add(job)
    s.flush()
    return job, True


def claim_jobs(s: Session, limit: int = 3) -> list[SearchJob]:
    """For the scraper machine: hand out the oldest waiting searches and mark them running."""
    now = utcnow()
    for stuck in s.scalars(
        select(SearchJob).where(SearchJob.status == "running", SearchJob.claimed_at < now - STALE_RUNNING)
    ).all():
        stuck.status = "pending"
    jobs = s.scalars(
        select(SearchJob).where(SearchJob.status == "pending").order_by(SearchJob.id).limit(limit)
    ).all()
    for j in jobs:
        j.status, j.claimed_at = "running", now
    s.flush()
    return list(jobs)


def finish_job(s: Session, job_id: int, found: int, error: str = "") -> SearchJob | None:
    job = s.get(SearchJob, job_id)
    if job is None or job.status == "done":
        return job
    job.finished_at = utcnow()
    job.found = max(0, int(found))
    if error:
        job.status, job.note = "failed", error[:200]
    else:
        job.status = "done"
    s.flush()
    return job
