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


def test_purge_sample_keeps_real_prices_and_accounts(session, settings, capsys):
    import admin
    from pricepulse.db import AppUser, LookupSession, Price, ScrapeRun, SessionItem

    ingest_items(session, [row("Checkers", "Example Thing 1L", 10)], "paarl", T, settings, is_sample=True)
    ingest_items(
        session, [row("Checkers", "Real Thing 2L", 20, url="https://x.example/r")], "paarl", T, settings
    )
    sample_pid = session.scalars(select(Product).where(Product.name.like("Example%"))).one().id
    real_pid = session.scalars(select(Product).where(Product.name.like("Real%"))).one().id
    session.add(AppUser(uid="u1", id_fingerprint="f" * 64, adult=True))
    sess = LookupSession(uid="u1", day="2026-11-10")
    sess.items.append(SessionItem(product_id=sample_pid))
    sess.items.append(SessionItem(product_id=real_pid))
    session.add_all(
        [
            sess,
            ScrapeRun(store="Checkers", region="paarl", status="sample"),
            ScrapeRun(store="Checkers", region="paarl", status="ok"),
        ]
    )
    session.flush()

    out = admin.purge_sample(session)
    assert out == {"prices": 1, "listings": 1, "products": 1, "runs": 1}
    assert [p.name for p in session.scalars(select(Product))] == ["Real Thing"]
    assert len(session.scalars(select(Price)).all()) == 1
    assert [i.product_id for i in session.scalars(select(SessionItem))] == [real_pid]
    assert session.get(AppUser, "u1").id_fingerprint == "f" * 64
    assert [r.status for r in session.scalars(select(ScrapeRun))] == ["ok"]
    assert admin.purge_sample(session) == {"prices": 0, "listings": 0, "products": 0, "runs": 0}
