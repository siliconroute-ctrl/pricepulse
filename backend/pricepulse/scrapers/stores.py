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

from typing import Any

from .base import BaseScraper, Item, walk_json_products


class Checkers(BaseScraper):
    """Checkers (Shoprite group). Server-rendered search page with product tiles.

    Guess: tiles carry a ``data-product-ga`` JSON attribute with name and price.
    """

    name = "Checkers"
    slug = "checkers"
    base_url = "https://www.checkers.co.za"
    search_url = "https://www.checkers.co.za/search/all?q={q}"
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


class PickNPay(BaseScraper):
    """Pick n Pay. Angular single-page site on SAP Commerce.

    Guess: the site calls a JSON product-search endpoint. The store code chooses
    the branch, which sets regional prices; put it in regions.yaml when found.
    """

    name = "Pick n Pay"
    slug = "pick-n-pay"
    base_url = "https://www.pnp.co.za"
    search_url = "https://www.pnp.co.za/search/{q}"
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
        rows = data.get("products") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return walk_json_products(data, self.name, self.base_url, category)
        items = []
        for p in rows:
            price = (p.get("price") or {}).get("value")
            promo = None
            for promo_block in p.get("potentialPromotions") or []:
                promo = promo_block.get("promotionalPrice", {}).get("value") or promo
            item = walk_json_products(
                {"name": p.get("name", ""), "price": price, "promoPrice": promo, "url": p.get("url", "")},
                self.name,
                self.base_url,
                category,
            )
            items.extend(item)
        return items


class Woolworths(BaseScraper):
    """Woolworths. Guess: the search page loads results from a JSON endpoint."""

    name = "Woolworths"
    slug = "woolworths"
    base_url = "https://www.woolworths.co.za"
    search_url = "https://www.woolworths.co.za/cat?Ntt={q}&Dy=1"
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


def find_scraper(name: str) -> type[BaseScraper] | None:
    """Look up a scraper by display name or slug, ignoring case and spaces."""
    key = name.lower().replace(" ", "").replace("-", "")
    for cls in ALL_SCRAPERS:
        if key in (cls.name.lower().replace(" ", "").replace("-", ""), cls.slug.replace("-", "")):
            return cls
    return None
