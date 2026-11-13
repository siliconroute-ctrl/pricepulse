"""The six store scrapers.

STATUS: NOT VERIFIED. None of these URLs or selectors has been checked against the
live websites yet - the build environment could not reach them. Each class lists
several candidate selectors and falls back to schema.org (JSON-LD) data, which gives
it a fair chance, but expect to fix things. Follow docs/FIX_A_SCRAPER.md:

    python run_scrapers.py --region paarl --store Checkers --debug

When a store is checked and working, set ``verified = True`` on its class so the
health report says so.
"""

from __future__ import annotations

import re
from typing import Any

from .base import BaseScraper, Item, make_item, walk_json_products


class Checkers(BaseScraper):
    """Checkers (Shoprite group).

    Search address confirmed by the owner (same one TrolleyUp uses):
    https://www.checkers.co.za/search?Search=<words>. A phone browser is sent to the separate
    "Sixty60" app, so this scraper presents as a desktop (see ``context_options``). The
    page markup below is still an UNVERIFIED guess. Checkers also uses AWS bot protection:
    if it shows a challenge page we stop and report BLOCKED, we never try to solve it.
    """

    name = "Checkers"
    slug = "checkers"
    base_url = "https://www.checkers.co.za"
    search_url = "https://www.checkers.co.za/search?Search={q}"
    json_first = True
    selectors = {
        "card": [
            ".product-frame",
            "[data-product-ga]",
            ".product__listing .item-product",
            "[data-testid='product-card']",
        ],
        "name": [
            ".item-product__name a",
            ".item-product__name",
            "[data-testid='product-title']",
            "h3 a",
            "h3",
        ],
        "price": [
            ".special-price__price .now",
            ".special-price__price",
            ".now",
            "[data-testid='product-price']",
            ".price",
        ],
        "promo": [".special-price__extra .now", ".promotion-price"],
        "was": [".special-price__extra .before", ".was-price", ".before"],
        "size": [".item-product__size", ".product-size"],
        "link": ["a.product-listening-click", ".item-product__name a", "a[href*='/p/']", "a"],
    }

    def parse_json(self, data: Any, category: str = "") -> list[Item]:
        """Read Checkers' product feed (``/api/catalogue/get-products-filter``).

        Verified against a real saved answer (tests/fixtures/checkers/). Fields used:
        ``price`` (rand), ``isOnPromotion`` / ``oldPrice`` (cents; equals ``price`` on the
        specials seen so far, so the usual price is then unknown), ``articleNumber`` +
        ``unitOfMeasure`` (the code at the end of the product's web address), ``brand``,
        ``barcodes``, ``hasAlcohol``, ``isStockAvailable`` and ``storeId``.
        """
        products = data.get("products") if isinstance(data, dict) else None
        if not isinstance(products, list) or not all(isinstance(p, dict) for p in products):
            return walk_json_products(data, self.name, self.base_url, category, self.api_minor_units)
        items: list[Item] = []
        for p in products:
            if p.get("hasTobacco"):
                continue  # not part of this app
            price = p.get("price")
            old = (p.get("oldPrice") or 0) / 100
            on_special = bool(p.get("isOnPromotion"))
            regular, promo = price, None
            if price is not None and old > float(price) + 0.004:  # the shop gave a higher usual price
                regular, promo, on_special = old, price, False
            offers = [
                str(b.get("shortDescription") or b.get("name") or "").strip()
                for b in p.get("bonusBuys") or []
                if isinstance(b, dict)
            ]
            code = f"{p.get('articleNumber', '')}{p.get('unitOfMeasure', '')}"
            link = f"/product/{checkers_slug(p.get('name', ''))}-{code}" if code else ""
            item = make_item(
                self.name,
                p.get("name", ""),
                regular,
                "Liquor" if p.get("hasAlcohol") or p.get("forOver18") else category,
                promo=promo,
                url=link,
                deal="; ".join(o for o in offers[:2] if o),
                base_url=self.base_url,
                brand=p.get("brand") or "",
                barcode=(p.get("barcodes") or [""])[0],
                store_ref=p.get("storeId") or (p.get("store") or {}).get("storeId", ""),
                special=on_special,
                in_stock=bool(p.get("isStockAvailable", True)) and not p.get("outOfStock", False),
            )
            if item:
                items.append(item)
        return items


def checkers_slug(name: str) -> str:
    """Checkers' web-address words for a product: "SASKO Low G.I Bread 800g" -> "sasko-low-gi-bread-800g"."""
    text = re.sub(r"['\u2019.]", "", name.lower().replace("&", " and "))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


class PickNPay(BaseScraper):
    """Pick n Pay. Angular single-page site on SAP Commerce (Hybris).

    Read from a real saved answer (tests/fixtures/pick-n-pay/search_feed.json). The page calls
    ``/pnphybris/v2/pnp-spa/products/search?...&query=<words>&storeCode=WC21``. ``WC21`` is the
    store an anonymous visitor gets; the store code is what sets the prices, so other regions
    will need a different store chosen on the site. The feed has no barcode.
    """

    name = "Pick n Pay"
    slug = "pick-n-pay"
    base_url = "https://www.pnp.co.za"
    search_url = "https://www.pnp.co.za/search/{q}"
    json_first = True
    # Guess kept for later; never called while use_api is False.
    api_url = (
        "https://www.pnp.co.za/pnphybris/v2/pnp-spa/products/search"
        "?query={q}&pageSize=20&fields=FULL&storeCode={store_code}"
    )
    selectors = {
        "card": [
            "cx-product-grid-item",
            "ui-product-grid-item",
            ".product-grid-item",
            "[data-cnstrc-item-id]",
        ],
        "name": [".product-grid-item__info-container__name", ".cx-product-name", "a.product-name", "h3"],
        "price": [".product-grid-item__price-container .price", ".cx-product-price", ".price"],
        "promo": [".product-grid-item__price-container .promotion-price", ".promo-price"],
        "was": [".product-grid-item__price-container .old-price", ".was-price"],
        "size": [],
        "link": ["a.product-grid-item__info-container__name", "a[href*='/p/']", "a"],
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.region_setting.setdefault("store_code", "")
        if not self.region_setting["store_code"]:
            self.api_url = self.api_url.replace("&storeCode={store_code}", "")

    def parse_json(self, data: Any, category: str = "") -> list[Item]:
        """Read Pick n Pay's product search answer. Other answers (menus, settings) give nothing.

        ``price.value`` is the shelf price. A higher ``oldPrice`` means a real was/now. Smart
        Shopper (loyalty card) and combo offers sit in ``potentialPromotions``: they are kept as
        text in ``deal`` and never replace the shelf price.
        """
        products = data.get("products") if isinstance(data, dict) else None
        if not isinstance(products, list) or not all(isinstance(p, dict) for p in products):
            return []
        match = re.search(r"storeCode=([A-Za-z0-9]+)", self.feed_url)
        store_ref = match.group(1) if match else ""
        items: list[Item] = []
        for p in products:
            if not isinstance(p.get("price"), dict) or not p.get("code"):
                continue
            value, old = p["price"].get("value"), p["price"].get("oldPrice") or 0
            regular, promo = value, None
            if value is not None and old > float(value) + 0.004:
                regular, promo = old, value
            offers = []
            for offer in p.get("potentialPromotions") or []:
                text = str(offer.get("promotionTextMessage") or "").strip()
                if text:
                    kind = str(offer.get("promotionDisplayType") or "").replace("_", " ").title()
                    offers.append(f"{kind}: {text}" if kind else text)
            stock = (p.get("stock") or {}).get("stockLevelStatus", "inStock")
            item = make_item(
                self.name,
                p.get("name", ""),
                regular,
                category,
                promo=promo,
                url=f"/{pnp_slug(p.get('name', ''))}/p/{p['code']}",
                base_url=self.base_url,
                store_ref=store_ref,
                in_stock=bool(p.get("available", True)) and stock != "outOfStock",
                deal="; ".join(offers[:2]),
            )
            if item:
                items.append(item)
        return items


def pnp_slug(name: str) -> str:
    """Pick n Pay's web-address words: "White Star Maize Meal 2.5kg" -> "white-star-maize-meal-2.5kg"."""
    text = re.sub(r"['\u2019]", "", name.lower().replace("&", " and "))
    return re.sub(r"[^a-z0-9.]+", "-", text).strip("-")


class Woolworths(BaseScraper):
    """Woolworths. The search page is run by Constructor.io (``cnstrc.com/v1/search/<words>``).

    Read from a real saved answer (tests/fixtures/woolworths/search_feed.json). Each result has
    the barcode (``data.id``, 13 digits; shorter ids are Woolworths' own codes), the description,
    a product path and three price columns ``p10``, ``p30``, ``p60`` with was-prices ``*_wp``
    (0 = no was-price). The three columns often differ for one product (e.g. 38.99 / 40.99 /
    39.99), so they look like price lists for different delivery areas or store groups.
    The owner compared the live site signed out (13 Nov 2026): the search listing shows ``p10``
    (milk 2 L R38.99) but the product page and the Woolworths app show ``p30`` (R40.99; Ayrshire
    milk 2 L was R47.99 now R42.99). The app price is what a shopper pays, so ``PRICE_COLUMN =
    "p30"``. The column used is saved in
    each price's store_ref in case other areas turn out to use the other columns.
    """

    name = "Woolworths"
    slug = "woolworths"
    base_url = "https://www.woolworths.co.za"
    search_url = "https://www.woolworths.co.za/cat?Ntt={q}&Dy=1"
    json_first = True
    PRICE_COLUMN = "p30"  # the price the product page and app show (owner checked)
    api_url = "https://www.woolworths.co.za/server/searchCategory?pageURL=%2Fcat&Ntt={q}&Dy=1"
    selectors = {
        "card": [".product-list__item", ".product--view", "[data-testid='product-card']"],
        "name": [".range--title", ".product-card__name", "a.product--desc", "h2"],
        "price": [".price", ".product__price .price", "strong.price"],
        "promo": [".product__price .price--discounted", ".price--promo"],
        "was": [".product__price .price--was", ".price--original"],
        "size": [],
        "link": ["a.range--title", "a.product--desc", "a"],
    }

    def parse_json(self, data: Any, category: str = "") -> list[Item]:
        """Read Woolworths' search answer. Other answers (menus, settings) give nothing."""
        response = data.get("response") if isinstance(data, dict) else None
        results = response.get("results") if isinstance(response, dict) else None
        if not isinstance(results, list) or not all(isinstance(r, dict) for r in results):
            return []
        col = self.PRICE_COLUMN
        items: list[Item] = []
        for r in results:
            d = r.get("data")
            if not isinstance(d, dict) or not d.get("url") or not isinstance(d.get(col), (int, float)):
                continue
            price, was = float(d[col]), d.get(f"{col}_wp") or 0
            regular, promo = price, None
            if isinstance(was, (int, float)) and was > price + 0.004:
                regular, promo = float(was), price
            code = str(d.get("id", ""))
            item = make_item(
                self.name,
                r.get("value") or d.get("description") or "",
                regular,
                "Liquor" if d.get("liquor") else category,
                promo=promo,
                url="/" + str(d["url"]).lstrip("/"),
                base_url=self.base_url,
                barcode=code if re.fullmatch(r"\d{13}", code) else "",
                store_ref=col,
            )
            if item:
                items.append(item)
        return items


class UltraLiquors(BaseScraper):
    """Ultra Liquors. Guess: a Shopify-style shop with a JSON search-suggest endpoint."""

    name = "Ultra Liquors"
    slug = "ultra-liquors"
    base_url = "https://www.ultraliquors.co.za"
    search_url = "https://www.ultraliquors.co.za/search?q={q}&type=product"
    api_url = (
        "https://www.ultraliquors.co.za/search/suggest.json"
        "?q={q}&resources%5Btype%5D=product&resources%5Blimit%5D=10"
    )
    selectors = {
        "card": [".product-card", ".grid-product", ".product-item", "li.grid__item", ".card-wrapper"],
        "name": [
            ".product-card__title",
            ".grid-product__title",
            ".product-item__title",
            ".card__heading a",
            "h3",
        ],
        "price": [".price-item--regular", ".product-price", ".price", ".money"],
        "promo": [".price-item--sale", ".price--on-sale .price-item--sale"],
        "was": [".price-item--regular s", "s.price-item--regular", ".compare-at"],
        "size": [],
        "link": ["a.product-card__link", ".card__heading a", "a[href*='/products/']", "a"],
    }

    def parse_json(self, data: Any, category: str = "") -> list[Item]:
        try:
            rows = data["resources"]["results"]["products"]
        except (KeyError, TypeError):
            return walk_json_products(data, self.name, self.base_url, category)
        items = []
        for p in rows:
            promo = None
            compare = p.get("compare_at_price_max") or p.get("compare_at_price")
            price = p.get("price")
            if compare and str(compare) not in ("0", "0.00") and str(compare) != str(price):
                price, promo = compare, price
            items.extend(
                walk_json_products(
                    {
                        "name": p.get("title", ""),
                        "price": price,
                        "promoPrice": promo,
                        "url": p.get("url", ""),
                    },
                    self.name,
                    self.base_url,
                    category,
                )
            )
        return items


class Liquorland(BaseScraper):
    """Liquorland. Guess: a WooCommerce shop, which offers a public Store API."""

    name = "Liquorland"
    slug = "liquorland"
    base_url = "https://www.liquorland.co.za"
    search_url = "https://www.liquorland.co.za/?s={q}&post_type=product"
    api_url = "https://www.liquorland.co.za/wp-json/wc/store/v1/products?search={q}&per_page=10"
    api_minor_units = True  # WooCommerce Store API gives "18999" for R189.99
    selectors = {
        "card": ["li.product", ".product-grid-item", ".product-small"],
        "name": [".woocommerce-loop-product__title", ".product-title", "h2", "h3"],
        "price": [".price > .amount", ".price .woocommerce-Price-amount", ".price"],
        "promo": [".price ins .amount", ".price ins"],
        "was": [".price del .amount", ".price del"],
        "size": [],
        "link": ["a.woocommerce-LoopProduct-link", "a"],
    }


class DisChem(BaseScraper):
    """Dis-Chem. Guess: Magento-style search results with data-price-amount attributes."""

    name = "Dis-Chem"
    slug = "dis-chem"
    base_url = "https://www.dischem.co.za"
    search_url = "https://www.dischem.co.za/catalogsearch/result/?q={q}"
    selectors = {
        "card": ["li.product-item", ".product-item-info", "[data-testid='product-card']"],
        "name": [".product-item-link", ".product-item-name a", ".product-name", "h2"],
        "price": ["[data-price-type='finalPrice']", ".price-final_price .price", ".price"],
        "promo": [".special-price [data-price-type='finalPrice']", ".special-price .price"],
        "was": ["[data-price-type='oldPrice']", ".old-price .price"],
        "size": [],
        "link": ["a.product-item-link", "a.product-item-photo", "a"],
    }


ALL_SCRAPERS: list[type[BaseScraper]] = [Checkers, PickNPay, Woolworths, UltraLiquors, Liquorland, DisChem]

# Searched by default (owner, 13 Nov 2026). Ultra Liquors and Liquorland are on hold: they stay
# in ALL_SCRAPERS so `--store "Ultra Liquors"` still works, but no normal run touches them.
ACTIVE_SCRAPERS: list[type[BaseScraper]] = [Checkers, PickNPay, Woolworths, DisChem]


def find_scraper(name: str) -> type[BaseScraper] | None:
    """Look up a scraper by display name or slug, ignoring case and spaces."""
    key = name.lower().replace(" ", "").replace("-", "")
    for cls in ALL_SCRAPERS:
        if key in (cls.name.lower().replace(" ", "").replace("-", ""), cls.slug.replace("-", "")):
            return cls
    return None
