from datetime import datetime

from conftest import row
from sqlalchemy import func, select

from pricepulse.db import Listing, Price, Product
from pricepulse.ingest import display_name, ingest_items, parse_scraped_at, read_scraper_json

T1 = datetime(2026, 10, 27, 6, 30)
T1_LATER = datetime(2026, 10, 27, 11, 0)  # same 06:00-12:00 slot as T1
T2 = datetime(2026, 10, 27, 13, 0)


def count(s, model):
    return s.scalar(select(func.count()).select_from(model))


def test_same_product_three_stores_is_one_product(session, settings):
    rows = [
        row("Checkers", "Clover Full Cream Milk 2L", 36.99),
        row("Pick n Pay", "Clover Fresh Full Cream Milk", 38.49, size="2 l"),
        row("Woolworths", "CLOVER FULL CREAM MILK 2LTR", 41.99),
    ]
    rep = ingest_items(session, rows, "paarl", T1, settings)
    assert (rep.new_products, rep.matched) == (1, 2)
    assert count(session, Product) == 1 and count(session, Price) == 3
    assert session.scalars(select(Product)).one().name == "Clover Full Cream Milk"


def test_rerun_in_same_slot_overwrites(session, settings):
    ingest_items(session, [row("Checkers", "Clover Milk 2L", 36.99)], "paarl", T1, settings)
    ingest_items(session, [row("Checkers", "Clover Milk 2L", 34.99)], "paarl", T1_LATER, settings)
    prices = session.scalars(select(Price)).all()
    assert len(prices) == 1 and prices[0].price_cents == 3499
    # A later slot adds history instead of overwriting.
    ingest_items(session, [row("Checkers", "Clover Milk 2L", 33.99)], "paarl", T2, settings)
    assert count(session, Price) == 2 and count(session, Listing) == 1


def test_regions_kept_apart(session, settings):
    ingest_items(session, [row("Checkers", "Clover Milk 2L", 36.99)], "paarl", T1, settings)
    ingest_items(session, [row("Checkers", "Clover Milk 2L", 37.99)], "worcester", T1, settings)
    assert sorted(p.region for p in session.scalars(select(Price))) == ["paarl", "worcester"]


def test_rejects_and_promo(session, settings):
    rows = [
        row("Checkers", "", 10),
        row("Checkers", "Thing 1L", 0),
        row("", "Thing 1L", 5),
        row("Checkers", "Thing 1L", "R12.99", promo="R9.99"),
        row("Pick n Pay", "Thing 1L", 12.0, promo=15.0),
    ]
    rep = ingest_items(session, rows, "paarl", T1, settings)
    assert len(rep.rejected) == 3 and rep.prices_written == 2
    by = {p.listing.store.name: p for p in session.scalars(select(Price))}
    assert (by["Checkers"].price_cents, by["Checkers"].promo_cents) == (1299, 999)
    assert by["Pick n Pay"].promo_cents is None  # a "promo" above the price is ignored


def test_review_queue(session, settings):
    settings.match_auto, settings.match_review = 99, 50
    ingest_items(session, [row("Checkers", "Albany Superior White Bread 700g", 19.99)], "paarl", T1, settings)
    rep = ingest_items(
        session, [row("Pick n Pay", "Albany Superior Brown Bread 700g", 19.99)], "paarl", T1, settings
    )
    assert rep.needs_review == 1
    lst = session.scalars(select(Listing).where(Listing.match_status == "needs_review")).one()
    assert lst.suggestion_id is not None and lst.product_id != lst.suggestion_id


def test_scraped_at_parsing():
    assert parse_scraped_at("2026-10-27 07:30") == datetime(2026, 10, 27, 5, 30)  # SAST -> UTC
    assert parse_scraped_at("2026-10-27T05:30:00Z") == datetime(2026, 10, 27, 5, 30)


def test_file_contract_both_shapes():
    assert read_scraper_json([{"store": "A"}]) == ("", "", [{"store": "A"}])
    assert read_scraper_json({"region": "paarl", "scraped_at": "x", "items": [1]}) == ("paarl", "x", [1])


def test_display_name():
    assert display_name("Clover Milk 2L", "2L") == "Clover Milk"
    assert display_name("Panado Tablets 500mg", "24 tablets") == "Panado Tablets 500mg"
