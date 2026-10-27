"""Orchestrator behaviour with a fake browser: no network, no Playwright."""

import json
import types

from pricepulse.scrapers.base import BaseScraper, BlockedError, Item
from run_scrapers import other_stores, previous_rows, read_terms, scrape_store, write_atomic


def test_products_txt_has_categories_and_enough_terms():
    terms = read_terms(__import__("pathlib").Path(__file__).parent.parent / "products.txt")
    cats = {c for _, c in terms}
    assert cats == {"Groceries", "Fresh", "Liquor", "Health & Beauty"}
    assert len(terms) >= 60  # original basket plus 30+ staples
    assert ("castle lite 24 x 340ml", "Liquor") in terms


def test_read_terms_headers(tmp_path):
    p = tmp_path / "p.txt"
    p.write_text("milk\n# a comment\n# category: Liquor\n\nbeer\n# Category:   Fresh \neggs\n")
    assert read_terms(p) == [("milk", "Groceries"), ("beer", "Liquor"), ("eggs", "Fresh")]


def test_write_atomic_and_previous_rows(tmp_path):
    path = tmp_path / "latest_paarl.json"
    write_atomic(
        path,
        {
            "region": "paarl",
            "scraped_at": "2026-10-27 07:30",
            "items": [{"store": "A", "name": "x", "price": 1}, {"store": "B", "name": "y", "price": 2}],
        },
    )
    assert json.loads(path.read_text())["region"] == "paarl"
    assert not list(tmp_path.glob("*.tmp"))
    kept = previous_rows(path, "B")
    assert kept == [{"store": "B", "name": "y", "price": 2, "scraped_at": "2026-10-27 07:30"}]
    assert previous_rows(tmp_path / "missing.json", "B") == []
    assert other_stores(path, {"A"}) == ["B"]
    assert other_stores(tmp_path / "missing.json", {"A"}) == []


class FakePage:
    def goto(self, *a, **k):
        return None

    def get_by_role(self, *a, **k):
        return types.SimpleNamespace(count=lambda: 0)


class FakeBrowser:
    def new_context(self, **kw):
        page = FakePage()
        return types.SimpleNamespace(new_page=lambda: page, close=lambda: None)


def _args(**kw):
    return types.SimpleNamespace(debug=False, save_fixtures=False, **kw)


def make_scraper(behaviour):
    class Fake(BaseScraper):
        name = "Fake"
        slug = "fake"
        base_url = "https://fake.example"

        def setup(self, page):
            pass

        def pause(self):
            pass

        def search(self, page, term, category=""):
            return behaviour(term, category)

    return Fake


def test_store_ok_and_partial(settings):
    def b(term, cat):
        return [Item("Fake", f"{term} thing", "1L", cat, 10.0)] if term != "missing" else []

    items, h = scrape_store(
        make_scraper(b), FakeBrowser(), "paarl", {}, [("milk", "Fresh"), ("missing", "Fresh")], _args()
    )
    assert len(items) == 1 and h.status == "partial" and h.terms_missing == ["missing"]


def test_blocked_store_stops_and_reports(settings):
    calls = []

    def b(term, cat):
        calls.append(term)
        if term == "b":
            raise BlockedError("HTTP 403")
        return [Item("Fake", "a thing", "", cat, 5.0)]

    items, h = scrape_store(
        make_scraper(b), FakeBrowser(), "paarl", {}, [("a", "X"), ("b", "X"), ("c", "X")], _args()
    )
    assert h.status == "blocked" and calls == ["a", "b"]
    assert h.terms_missing == ["b", "c"] and len(items) == 1
    assert "BLOCKED" in h.summary()


def test_errors_in_one_term_do_not_stop_the_store(settings):
    def b(term, cat):
        if term == "bad":
            raise RuntimeError("timeout")
        return [Item("Fake", f"{term} x", "", cat, 5.0)]

    items, h = scrape_store(
        make_scraper(b), FakeBrowser(), "paarl", {}, [("bad", "X"), ("good", "X")], _args()
    )
    assert len(items) == 1 and h.errors and h.status == "partial"


def test_setup_failure_fails_only_that_store(settings):
    class Broken(make_scraper(lambda t, c: [])):
        def setup(self, page):
            raise RuntimeError("site down")

    items, h = scrape_store(Broken, FakeBrowser(), "paarl", {}, [("a", "X")], _args())
    assert items == [] and h.status == "failed" and "site down" in h.errors[0]
