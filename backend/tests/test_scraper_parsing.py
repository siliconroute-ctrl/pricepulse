"""Scraper parsing on saved pages. No network. See fixtures/README.md: the fixtures
are synthetic until replaced with real saved pages."""

import json

import pytest
from conftest import FIXTURES

from pricepulse.scrapers.base import (
    Item,
    StoreHealth,
    check_items,
    looks_blocked,
    make_item,
    parse_json_ld,
    relevant,
)
from pricepulse.scrapers.stores import (
    ALL_SCRAPERS,
    Checkers,
    DisChem,
    Liquorland,
    PickNPay,
    UltraLiquors,
    Woolworths,
    find_scraper,
)


def html(path):
    return (FIXTURES / path).read_text(encoding="utf-8")


def js(path):
    return json.loads((FIXTURES / path).read_text(encoding="utf-8"))


def test_checkers_html():
    items = Checkers().parse_html(html("checkers/search_milk.html"), "Fresh")
    by_name = {i.name: i for i in items}
    milk = by_name["Clover Full Cream Fresh Milk 2L"]
    assert milk.price == 36.99 and milk.size == "2L" and milk.category == "Fresh"
    assert milk.url.startswith("https://www.checkers.co.za/All-Departments/")
    low = by_name["Clover Low Fat Fresh Milk 2L"]
    assert (low.price, low.promo_price) == (39.99, 35.99)  # was/now parsed as regular/promo
    # Price taken from the data-product-ga attribute when the tile has no price element.
    assert by_name["Ritebrand Long Life Milk 1L"].price == 14.99
    assert all(i.price > 0 and i.name for i in items)  # empty card with R0.00 dropped
    assert len(items) == 3


def test_pick_n_pay_json_and_html():
    items = PickNPay().parse_json(js("pick-n-pay/search_milk.json"))
    assert [(i.name, i.price, i.promo_price) for i in items] == [
        ("Clover Fresh Full Cream Milk 2l", 38.49, None),
        ("PnP Full Cream Fresh Milk 2l", 32.99, 29.99),
    ]
    assert items[0].url == "https://www.pnp.co.za/clover-fresh-full-cream-milk-2l/p/000000000000123456_EA"
    page = PickNPay().parse_html(html("pick-n-pay/search_milk.html"))
    assert [(i.name, i.price) for i in page] == [("Clover Fresh Full Cream Milk 2l", 38.49)]


def test_pick_n_pay_store_code_optional():
    assert "storeCode" not in PickNPay().api_url
    assert "storeCode=WC21" in PickNPay(region_setting={"store_code": "WC21"}).api_url.format(
        q="milk", store_code="WC21"
    )


def test_woolworths_json():
    items = Woolworths().parse_json(js("woolworths/search_milk.json"))
    assert [(i.name, i.size, i.price) for i in items] == [
        ("Full Cream Fresh Milk 2 L", "2 L", 31.99),
        ("Clover Full Cream Milk 2 L", "2 L", 41.99),
    ]
    assert items[0].url.startswith("https://www.woolworths.co.za/prod/")


def test_ultra_liquors_json_promo():
    items = UltraLiquors().parse_json(js("ultra-liquors/search_castle.json"), "Liquor")
    assert (items[0].name, items[0].price, items[0].promo_price) == (
        "Castle Lite Can 24 x 340ml",
        329.99,
        None,
    )
    assert (items[1].price, items[1].promo_price) == (119.99, 109.99)
    assert items[0].size == "24 x 340ml"


def test_liquorland_api_minor_units_and_html():
    items = Liquorland().parse_json(js("liquorland/search_castle.json"))
    assert [(i.name, i.price, i.promo_price) for i in items] == [
        ("Castle Lite 24 x 340ml Cans", 339.99, None),
        ("Klipdrift Premium Brandy 750ml", 194.99, 179.99),
    ]
    page = Liquorland().parse_html(html("liquorland/search_castle.html"))
    assert [(i.price, i.promo_price) for i in page] == [(339.99, None), (194.99, 179.99)]


def test_dischem_html():
    items = DisChem().parse_html(html("dis-chem/search_panado.html"), "Health & Beauty")
    assert [(i.name, i.size, i.price, i.promo_price) for i in items] == [
        ("Panado Tablets 24s", "24s", 34.95, None),
        ("Panado Capsules 48s", "48s", 69.95, 59.95),
    ]


def test_json_ld_fallback():
    items = parse_json_ld(html("jsonld/product_list.html"), "Dis-Chem", "https://www.dischem.co.za")
    assert {(i.name, i.price) for i in items} == {
        ("Dettol Antiseptic Liquid 500ml", 79.95),
        ("Dettol Antiseptic Liquid 1L", 139.95),
    }
    # The generic parse_html falls back to JSON-LD when no cards match.
    assert len(DisChem().parse_html(html("jsonld/product_list.html"))) == 2


def test_make_item_rules():
    assert make_item("X", "", "R10") is None
    assert make_item("X", "Thing", "R0") is None
    assert make_item("X", "Thing", "free") is None
    swapped = make_item("X", "Thing 1L", "R10", promo="R12")
    assert (swapped.price, swapped.promo_price) == (12, 10)
    same = make_item("X", "Thing", "R10", promo="R10")
    assert same.promo_price is None


def test_relevance_filter():
    assert relevant("clover full cream milk 2l", "Clover Full Cream Fresh Milk 2L")
    assert not relevant("clover full cream milk 2l", "Clover Low Fat Milk 2L")
    assert relevant("castle lite 24 x 340ml", "Castle Lite Can 24 x 340ml")
    assert not relevant("large eggs 18", "Cadbury Easter Eggs")


def test_block_detection():
    assert looks_blocked(403, "")
    assert looks_blocked(429, "")
    assert looks_blocked(200, "<html>Please complete the CAPTCHA</html>")
    assert not looks_blocked(200, "<html>Milk R36.99</html>")


def test_check_items_flags_suspect_and_rejects():
    h = StoreHealth(store="X", region="paarl")
    items = [
        Item("X", "Cheap", "", "", 0.5),
        Item("X", "Dear", "", "", 6000.0),
        Item("X", "Fine", "", "", 20.0),
        Item("X", "", "", "", 20.0),
    ]
    good = check_items(items, h)
    assert [i.name for i in good] == ["Cheap", "Dear", "Fine"]
    assert len(h.suspect) == 2 and len(h.rejects) == 1


def test_health_summary_plain_language():
    h = StoreHealth(store="Checkers", region="paarl", terms_total=23, terms_found=14, terms_missing=["a"] * 9)
    h.finish()
    assert h.status == "partial"
    assert h.summary() == "Checkers: 14 of 23 products found, 9 not found."
    b = StoreHealth(store="Woolworths", region="paarl", status="blocked", terms_total=5)
    b.finish()
    assert "BLOCKED" in b.summary()


def test_registry():
    assert len(ALL_SCRAPERS) == 6
    assert find_scraper("pick n pay") is PickNPay
    assert find_scraper("Dis-Chem") is DisChem and find_scraper("dischem") is DisChem
    assert find_scraper("nope") is None
    assert len({c.slug for c in ALL_SCRAPERS}) == 6


@pytest.mark.parametrize("cls", ALL_SCRAPERS)
def test_every_scraper_has_urls_and_selectors(cls):
    assert cls.base_url.startswith("https://")
    assert "{q}" in cls.search_url
    assert cls.selectors.get("card") and cls.selectors.get("name") and cls.selectors.get("price")


def test_failed_store_says_why():
    h = StoreHealth(store="Checkers", region="paarl", terms_total=2)
    h.errors.append("Could not open the website: Page.goto: net::ERR_NAME_NOT_RESOLVED at https://x/")
    h.status = "failed"
    assert "could not open the website (website address not found)" in h.summary()
    nothing = StoreHealth(store="Checkers", region="paarl", terms_total=2)
    nothing.finish()
    assert "nothing found; see the debug folder" in nothing.summary()
