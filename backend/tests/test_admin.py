from datetime import datetime

from conftest import row
from sqlalchemy import select

from admin import match_report, merge, review
from pricepulse.db import Listing, Product
from pricepulse.ingest import ingest_items

T = datetime(2026, 10, 27, 6)


def test_report(session, settings):
    ingest_items(
        session,
        [
            row("Checkers", "Clover Milk 2L", 30),
            row("Pick n Pay", "Clover Milk 2L", 31),
            row("Checkers", "Koo Beans 410g", 15),
        ],
        "paarl",
        T,
        settings,
    )
    r = match_report(session)
    assert r["listings"] == 3 and r["products"] == 2 and r["products_at_2plus_stores"] == 1
    assert r["match_rate"] == 50.0


def test_review_accept_merges(session, settings):
    settings.match_auto, settings.match_review = 99, 50
    ingest_items(
        session,
        [
            row("Checkers", "Albany Superior White Bread 700g", 19.99),
            row("Pick n Pay", "Albany Superior Brown Bread 700g", 19.99),
        ],
        "paarl",
        T,
        settings,
    )
    said = []
    review(session, ask=lambda q: "y", say=said.append)
    session.flush()
    assert {lst.product_id for lst in session.scalars(select(Listing))} == {1}
    assert len(session.scalars(select(Product)).all()) == 1  # placeholder product removed
    assert any("Merged" in s for s in said)


def test_review_reject_keeps_separate(session, settings):
    settings.match_auto, settings.match_review = 99, 50
    ingest_items(
        session,
        [
            row("Checkers", "Albany Superior White Bread 700g", 19.99),
            row("Pick n Pay", "Albany Superior Brown Bread 700g", 19.99),
        ],
        "paarl",
        T,
        settings,
    )
    review(session, ask=lambda q: "n", say=lambda s: None)
    assert [lst.match_status for lst in session.scalars(select(Listing).order_by(Listing.id))] == [
        "new",
        "new",
    ]


def test_merge(session, settings):
    ingest_items(
        session, [row("Checkers", "Thing A 1L", 1), row("Checkers", "Other B 1L", 2)], "paarl", T, settings
    )
    assert merge(session, 1, 2) == 1
    assert {lst.product_id for lst in session.scalars(select(Listing))} == {1}
