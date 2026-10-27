"""Shared scraper machinery: polite fetching, parsing helpers, health reporting.

Each store scraper (see stores.py) supplies URLs and selectors. Parsing is done by
pure functions (``parse_html`` / ``parse_json``) so it can be tested on saved pages
without touching the network.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from ..money import clean_price, is_suspect
from ..sizes import split_size

log = logging.getLogger("pricepulse.scraper")
SAST = timezone(timedelta(hours=2))

BLOCK_MARKERS = (
    "captcha",
    "cf-challenge",
    "challenge-platform",
    "access denied",
    "are you a robot",
    "request unsuccessful. incapsula",
    "pardon our interruption",
)


@dataclass
class Item:
    """One product row as found on a store's website."""

    store: str
    name: str
    size: str
    category: str
    price: float
    promo_price: float | None = None
    url: str = ""
    brand: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["promo_price"] is None:
            d.pop("promo_price")
        if not d["brand"]:
            d.pop("brand")
        return d


class BlockedError(Exception):
    """The site refused us (HTTP 403/429 or a CAPTCHA page)."""


@dataclass
class StoreHealth:
    """Per-store result of one run, for the plain-language health report."""

    store: str
    region: str
    pricing: str = "unknown"
    status: str = "ok"  # ok, partial, blocked, failed, skipped
    terms_total: int = 0
    terms_found: int = 0
    terms_missing: list[str] = field(default_factory=list)
    items: int = 0
    errors: list[str] = field(default_factory=list)
    rejects: list[str] = field(default_factory=list)
    suspect: list[str] = field(default_factory=list)
    started: str = ""
    finished: str = ""

    def finish(self) -> None:
        self.finished = datetime.now(SAST).strftime("%Y-%m-%d %H:%M:%S")
        if self.status in ("blocked", "failed", "skipped"):
            return
        if self.terms_found == 0:
            self.status = "failed"
        elif self.terms_missing:
            self.status = "partial"

    def summary(self) -> str:
        """One line a non-developer can read."""
        head = f"{self.store}: {self.terms_found} of {self.terms_total} products found"
        if self.terms_missing:
            head += f", {len(self.terms_missing)} not found"
        if self.status == "blocked":
            head += ". BLOCKED by the website - stopped this store and moved on"
        elif self.status == "failed":
            opened = next((e for e in self.errors if e.startswith("Could not open the website")), "")
            if opened:
                head += f". FAILED - could not open the website ({_net_reason(opened)})"
            else:
                head += ". FAILED - nothing found; see the debug folder"
        elif self.status == "skipped":
            head = f"{self.store}: skipped ({self.errors[0] if self.errors else 'no reason'})"
        extra = []
        if self.suspect:
            extra.append(f"{len(self.suspect)} suspect prices")
        if self.rejects:
            extra.append(f"{len(self.rejects)} rows rejected")
        if self.errors and self.status != "skipped":
            extra.append(f"{len(self.errors)} errors")
        return head + (" (" + ", ".join(extra) + ")" if extra else "") + "."


_NET_REASONS = {
    "ERR_NAME_NOT_RESOLVED": "website address not found",
    "ERR_INTERNET_DISCONNECTED": "no internet connection",
    "ERR_TUNNEL_CONNECTION_FAILED": "this network does not allow the website",
    "ERR_PROXY_CONNECTION_FAILED": "network proxy refused the connection",
    "ERR_CONNECTION_REFUSED": "the website refused the connection",
    "ERR_CONNECTION_TIMED_OUT": "the website did not answer",
    "ERR_TIMED_OUT": "the website did not answer",
    "ERR_CERT": "security certificate problem",
}


def _net_reason(error: str) -> str:
    """Turn a browser network error into plain words."""
    m = re.search(r"net::(ERR_[A-Z_]+)", error)
    if not m:
        return "timed out" if "Timeout" in error else "see data/health report"
    code = m.group(1)
    return next((v for k, v in _NET_REASONS.items() if code.startswith(k)), code)


# --- Parsing helpers (pure, no network) ---------------------------------------


def _first(el: Any, selectors: list[str]) -> Any:
    for sel in selectors:
        found = el.select_one(sel)
        if found is not None:
            return found
    return None


def _text(el: Any) -> str:
    if el is None:
        return ""
    for attr in ("data-price-amount", "content", "data-price", "title"):
        if el.has_attr(attr) and el[attr].strip():
            return el[attr].strip()
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True))


def make_item(
    store: str,
    name: str,
    price: Any,
    category: str = "",
    size: str = "",
    promo: Any = None,
    url: str = "",
    base_url: str = "",
    brand: str = "",
) -> Item | None:
    """Build an Item, splitting size out of the name and handling promo vs regular."""
    name = re.sub(r"\s+", " ", (name or "")).strip()
    p = clean_price(price)
    pr = clean_price(promo)
    if not name or p is None:
        return None
    # When the "promo" is higher, the two were swapped (was/now labels differ by site).
    if pr is not None and pr > p:
        p, pr = pr, p
    if pr is not None and pr == p:
        pr = None
    if not size:
        # Keep the full name for display; the size is split out for matching.
        _, size = split_size(name)
    if url and base_url:
        url = urljoin(base_url, url)
    return Item(
        store=store,
        name=name,
        size=size,
        category=category,
        price=p,
        promo_price=pr,
        url=url,
        brand=brand or "",
    )


def parse_html_cards(
    html: str, store: str, base_url: str, selectors: dict[str, list[str]], category: str = ""
) -> list[Item]:
    """Parse product cards using lists of candidate CSS selectors.

    ``selectors`` keys: card, name, price, promo, was, size, link. The first
    selector that matches wins, so several site layouts can be listed.
    """
    soup = BeautifulSoup(html, "html.parser")
    cards: list[Any] = []
    for sel in selectors.get("card", []):
        cards = soup.select(sel)
        if cards:
            break
    items = []
    for card in cards:
        ga = card.get("data-product-ga") if hasattr(card, "get") else None
        ga_data: dict = {}
        if ga:
            try:
                ga_data = json.loads(ga)
            except ValueError:
                ga_data = {}
        name_el = _first(card, selectors.get("name", []))
        name = _text(name_el) or str(ga_data.get("name", ""))
        now_el = _first(card, selectors.get("promo", []))
        was_el = _first(card, selectors.get("was", []))
        price_el = _first(card, selectors.get("price", []))
        price_txt = _text(price_el) or str(ga_data.get("price", ""))
        promo_txt = ""
        if now_el is not None and was_el is not None:
            # On promotion: "was" is the regular price, "now" the promo price.
            price_txt, promo_txt = _text(was_el), _text(now_el)
        size = _text(_first(card, selectors.get("size", [])))
        link_el = _first(card, selectors.get("link", [])) or (
            name_el if name_el is not None and name_el.name == "a" else None
        )
        href = link_el.get("href", "") if link_el is not None else ""
        item = make_item(store, name, price_txt, category, size, promo_txt, href, base_url)
        if item:
            items.append(item)
    return items


def parse_json_ld(html: str, store: str, base_url: str, category: str = "") -> list[Item]:
    """Fallback: read schema.org Product data that many shops embed for Google."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[Item] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except ValueError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            if node.get("@type") == "ItemList":
                stack.extend(e.get("item", e) for e in node.get("itemListElement", []) if isinstance(e, dict))
            elif node.get("@type") == "Product":
                offers = node.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                price = offers.get("price") or offers.get("lowPrice")
                brand = node.get("brand")
                if isinstance(brand, dict):
                    brand = brand.get("name", "")
                item = make_item(
                    store,
                    node.get("name", ""),
                    price,
                    category,
                    url=node.get("url", "") or offers.get("url", ""),
                    base_url=base_url,
                    brand=brand or "",
                )
                if item:
                    items.append(item)
            else:
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
    return items


NAME_KEYS = ("name", "displayName", "p_displayName", "productName", "title", "Name")
PRICE_KEYS = ("price", "p_pl10", "salePrice", "sellingPrice", "currentPrice", "Price", "amount")
PROMO_KEYS = ("promoPrice", "promotionPrice", "specialPrice", "sale_price", "wasPrice")
URL_KEYS = ("url", "detailPageURL", "productUrl", "permalink", "link", "handle")


def _price_value(v: Any) -> Any:
    """Prices may be numbers, strings, or {"value": 36.99} / {"formattedValue": "R36.99"}."""
    if isinstance(v, dict):
        for k in ("value", "amount", "price", "formattedValue"):
            if k in v:
                return _price_value(v[k])
        return None
    return v


def walk_json_products(
    data: Any, store: str, base_url: str, category: str = "", minor_units: bool = False
) -> list[Item]:
    """Generic fallback: find dicts that look like products anywhere in a JSON reply."""
    items: list[Item] = []
    seen: set[tuple[str, float]] = set()
    stack = [data]
    while stack:
        node = stack.pop(0)
        if isinstance(node, list):
            stack.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        holder = node.get("attributes") if isinstance(node.get("attributes"), dict) else node
        name = next((holder[k] for k in NAME_KEYS if isinstance(holder.get(k), str)), "")
        price = next((_price_value(node[k]) for k in PRICE_KEYS if k in node), None)
        if price is None and isinstance(node.get("prices"), dict):
            price = node["prices"].get("regular_price") or node["prices"].get("price")
        if price is None and isinstance(node.get("startingPrice"), dict):
            price = node["startingPrice"].get("p_pl10")
        if name and price not in (None, ""):
            promo = next((_price_value(node[k]) for k in PROMO_KEYS if k in node), None)
            if promo is None and isinstance(node.get("prices"), dict):
                sale = node["prices"].get("sale_price")
                if sale and sale != node["prices"].get("regular_price"):
                    promo = sale
            if minor_units:
                price = int(price) / 100 if str(price).isdigit() else price
                promo = int(promo) / 100 if promo is not None and str(promo).isdigit() else promo
            url = next((str(node[k]) for k in URL_KEYS if node.get(k)), "")
            item = make_item(store, name, price, category, promo=promo, url=url, base_url=base_url)
            if item and (item.name, item.price) not in seen:
                seen.add((item.name, item.price))
                items.append(item)
                continue
        stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
    return items


STOP_WORDS = {"the", "and", "of", "in", "x"}


def relevant(term: str, name: str) -> bool:
    """Keep results whose name contains every word of the search term.

    Size words in the term ("2l") are ignored because sites format sizes differently.
    """
    name_l = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    words = [w for w in re.sub(r"[^a-z0-9 ]", " ", term.lower()).split() if w not in STOP_WORDS]
    words = [w for w in words if not re.fullmatch(r"\d+(?:[.,]\d+)?(?:kg|g|ml|l|ltr|s|pack)?", w)]
    return all(re.search(rf"\b{re.escape(w)}", name_l) for w in words)


def looks_blocked(status: int, body: str) -> bool:
    if status in (403, 429):
        return True
    low = body[:20000].lower()
    return any(m in low for m in BLOCK_MARKERS) and len(low) < 200000


# --- Store scraper base class ---------------------------------------------------


class BaseScraper:
    """Base class. Subclasses set the class attributes and may override hooks.

    Network work runs through one Playwright browser context per store, opened by
    run_scrapers.py. Nothing here runs in tests.
    """

    name = "Store"
    slug = "store"
    base_url = ""
    search_url = ""  # with {q}
    api_url = ""  # JSON search endpoint with {q}; tried first when set
    api_minor_units = False  # API prices are in cents
    selectors: dict[str, list[str]] = {}
    verified = False  # set True only after checking against the live site
    min_delay = 1.5
    max_delay = 3.5
    max_blocks = 3
    retries = 2
    per_term = 3

    def __init__(
        self,
        region: str = "paarl",
        region_setting: dict | None = None,
        debug: bool = False,
        debug_dir: Path | None = None,
        fixture_dir: Path | None = None,
        backoff_seconds: float = 30.0,
    ) -> None:
        self.region = region
        self.region_setting = region_setting or {}
        self.debug = debug
        self.debug_dir = debug_dir
        self.fixture_dir = fixture_dir
        self.backoff_seconds = backoff_seconds
        self.blocks = 0

    # Hooks -----------------------------------------------------------------
    def parse_html(self, html: str, category: str = "") -> list[Item]:
        items = parse_html_cards(html, self.name, self.base_url, self.selectors, category)
        return items or parse_json_ld(html, self.name, self.base_url, category)

    def parse_json(self, data: Any, category: str = "") -> list[Item]:
        return walk_json_products(data, self.name, self.base_url, category, self.api_minor_units)

    def setup(self, page: Any) -> None:
        """Open the home page and apply the region setting. Override per store."""
        page.goto(self.base_url, wait_until="domcontentloaded", timeout=45000)
        self._dismiss_cookies(page)

    # Machinery -------------------------------------------------------------
    def _dismiss_cookies(self, page: Any) -> None:
        for label in ("Accept", "Accept all", "Accept All Cookies", "I agree", "Got it"):
            try:
                btn = page.get_by_role("button", name=label, exact=False)
                if btn.count():
                    btn.first.click(timeout=2000)
                    return
            except Exception:  # noqa: BLE001 - optional nicety, never fatal
                continue

    def pause(self) -> None:
        time.sleep(random.uniform(self.min_delay, self.max_delay))

    def _blocked(self, why: str) -> None:
        self.blocks += 1
        wait = self.backoff_seconds * self.blocks
        log.warning(
            "%s: the website refused us (%s). Waiting %.0f s before trying again.", self.name, why, wait
        )
        if self.blocks >= self.max_blocks:
            raise BlockedError(why)
        time.sleep(wait)

    def _save_fixture(self, term: str, body: str, ext: str) -> None:
        if not self.fixture_dir:
            return
        d = self.fixture_dir / self.slug
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{re.sub(r'[^a-z0-9]+', '_', term.lower())}.{ext}").write_text(body, encoding="utf-8")

    def _search_api(self, page: Any, term: str, category: str) -> list[Item]:
        url = self.api_url.format(q=quote_plus(term), **self.region_setting)
        resp = page.request.get(url, timeout=30000, headers={"Accept": "application/json"})
        body = resp.text()
        if looks_blocked(resp.status, body):
            self._blocked(f"HTTP {resp.status} on search API")
            return []
        if resp.status >= 400:
            raise RuntimeError(f"search API returned HTTP {resp.status}")
        self._save_fixture(term, body, "json")
        return self.parse_json(json.loads(body), category)

    def _search_page(self, page: Any, term: str, category: str) -> list[Item]:
        url = self.search_url.format(q=quote_plus(term), **self.region_setting)
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        status = resp.status if resp else 0
        card_sel = ", ".join(self.selectors.get("card", [])) or "body"
        try:
            page.wait_for_selector(card_sel, timeout=12000)
        except Exception:  # noqa: BLE001 - no cards is a normal outcome
            pass
        html = page.content()
        if looks_blocked(status, html):
            self._blocked(f"HTTP {status} or CAPTCHA on search page")
            return []
        self._save_fixture(term, html, "html")
        items = self.parse_html(html, category)
        if not items and self.debug_dir:
            d = self.debug_dir / self.slug
            d.mkdir(parents=True, exist_ok=True)
            stem = re.sub(r"[^a-z0-9]+", "_", term.lower())
            (d / f"{stem}.html").write_text(html, encoding="utf-8")
            try:
                page.screenshot(path=str(d / f"{stem}.png"), full_page=True)
            except Exception:  # noqa: BLE001
                pass
            log.info("%s: nothing found for '%s'. Saved page and screenshot to %s", self.name, term, d)
        return items

    def search(self, page: Any, term: str, category: str = "") -> list[Item]:
        """Search one term: JSON API first (if any), page parsing as fallback."""
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                items: list[Item] = []
                if self.api_url:
                    try:
                        items = self._search_api(page, term, category)
                    except BlockedError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - fall back to the page
                        log.debug("%s: API search failed for '%s': %s", self.name, term, exc)
                if not items and self.search_url:
                    items = self._search_page(page, term, category)
                self.blocks = 0 if items else self.blocks
                hits = [i for i in items if relevant(term, i.name)]
                return hits[: self.per_term]
            except BlockedError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"{self.name}: '{term}' failed after {self.retries + 1} tries: {last_error}")


def check_items(items: list[Item], health: StoreHealth) -> list[Item]:
    """Reject rows with no name or non-positive price; flag suspect prices."""
    good = []
    for it in items:
        if not it.name or it.price is None or it.price <= 0:
            health.rejects.append(f"{it.name or '(no name)'}: no usable price")
            continue
        if is_suspect(it.price):
            health.suspect.append(f"{it.name} R{it.price:.2f}")
        good.append(it)
    return good
