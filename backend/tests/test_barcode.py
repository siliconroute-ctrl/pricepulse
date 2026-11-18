"""Matching by barcode: exact, safe, and it upgrades listings loaded before barcodes existed."""

from datetime import datetime

from conftest import row
from sqlalchemy import func, select, text

from pricepulse.db import (
    Listing,
    LookupSession,
    Product,
    SessionItem,
    make_engine,
    migrate,
)
from pricepulse.ingest import clean_barcode, ingest_items

T = datetime(2026, 11, 14, 12, 0)
T2 = datetime(2026, 11, 15, 12, 0)


def bc(r, code):
    return {**r, "barcode": code}


def products(s):
    return s.scalar(select(func.count()).select_from(Product))


def test_clean_barcode_accepts_real_lengths_only():
    assert clean_barcode("6001231000191") == "6001231000191"
    assert clean_barcode(" 6001231000191 ") == "6001231000191"
    assert clean_barcode("96385074") == "96385074"
    assert clean_barcode("0000000000000") == ""
    assert clean_barcode("12345") == "" and clean_barcode(None) == "" and clean_barcode("abc") == ""


def test_same_barcode_joins_products_whatever_the_names(session, settings):
    rows = [
        bc(row("Checkers", "Iwisa No.1 Super Maize Meal Bag 2.5kg", 49.99), "6001205051501"),
        bc(row("Woolworths", "Iwisa No 1 Super Maize Meal 2.5 kg", 52.99), "6001205051501"),
    ]
    rep = ingest_items(session, rows, "paarl", T, settings)
    assert products(session) == 1 and rep.by_barcode == 1 and rep.matched == 1
    joined = session.scalars(select(Listing).where(Listing.match_reason == "same barcode")).one()
    assert joined.match_status == "exact" and joined.match_score == 100
    assert "joined by barcode" in rep.summary()


def test_different_barcodes_are_not_joined_by_this_rule(session, settings):
    rows = [
        bc(row("Checkers", "Vaseline Blue Seal Vitamin E Petroleum Jelly 250ml", 39.99), "6001000000011"),
        bc(row("Woolworths", "Vaseline Blue Seal Original Petroleum Jelly 250ml", 41.99), "6001000000028"),
    ]
    rep = ingest_items(session, rows, "paarl", T, settings)
    assert rep.by_barcode == 0


def test_same_store_never_joins_itself(session, settings):
    rows = [
        bc(row("Checkers", "Castle Lite 6 x 500ml", 114.99, url="https://c.example/a"), "6003326011945"),
        bc(row("Checkers", "Castle Lite Cans 6 x 500ml", 114.99, url="https://c.example/b"), "6003326011945"),
    ]
    rep = ingest_items(session, rows, "paarl", T, settings)
    assert rep.by_barcode == 0


def test_barcode_beats_a_review_queue_guess(session, settings):
    a = bc(row("Pick n Pay", "Jameson Select Reserve Irish Whisky", 599.0), "5011007003234")
    b = bc(
        row("Checkers", "Jameson Select Reserve Blended Irish Whiskey Bottle 750ml", 619.0), "5011007003234"
    )
    ingest_items(session, [{**a, "size": "750ml"}], "paarl", T, settings)
    rep = ingest_items(session, [b], "paarl", T, settings)
    assert products(session) == 1 and rep.by_barcode == 1 and rep.needs_review == 0


def test_older_listings_join_when_they_learn_their_barcode(session, settings):
    plain = [
        row("Checkers", "Tastic Long Grain Parboiled White Rice 2kg", 59.99, url="https://c.example/t"),
        row("Woolworths", "Tastic Premium Choice Rice 2 kg", 61.99, url="https://w.example/t"),
    ]
    ingest_items(session, plain, "paarl", T, settings)
    assert products(session) == 2  # names alone did not match (as on the live server)
    again = [bc(r, "6001231000191") for r in plain]
    rep = ingest_items(session, again, "paarl", T2, settings)
    assert rep.by_barcode == 1
    assert products(session) == 1  # the emptied product is gone
    assert {lst.match_reason for lst in session.scalars(select(Listing)).all()} >= {"same barcode"}


def test_session_items_follow_a_retired_product(session, settings):
    plain = [
        row("Checkers", "Tastic Long Grain Parboiled White Rice 2kg", 59.99, url="https://c.example/t"),
        row("Woolworths", "Tastic Parboiled Rice 2 kg", 61.99, url="https://w.example/t"),
    ]
    ingest_items(session, plain, "paarl", T, settings)
    w_product = session.scalar(select(Listing.product_id).where(Listing.ident == "https://w.example/t"))
    lookup = LookupSession(uid=_user(session), day="2026-11-14")
    session.add(lookup)
    session.flush()
    session.add(SessionItem(session_id=lookup.id, product_id=w_product))
    session.flush()
    ingest_items(session, [bc(r, "6001231000191") for r in plain], "paarl", T2, settings)
    item = session.scalars(select(SessionItem)).one()
    assert session.get(Product, item.product_id) is not None  # still points at a real product


def _user(session):
    from pricepulse.db import AppUser

    session.add(AppUser(uid="u1"))
    session.flush()
    return "u1"


def test_a_listing_already_shared_by_name_is_left_alone(session, settings):
    rows = [
        row("Checkers", "Clover Full Cream Milk 2L", 36.99, url="https://c.example/m"),
        row("Pick n Pay", "Clover Fresh Full Cream Milk", 38.49, size="2 l", url="https://p.example/m"),
    ]
    ingest_items(session, rows, "paarl", T, settings)
    assert products(session) == 1
    rep = ingest_items(session, [bc(rows[0], "6001000000011")], "paarl", T2, settings)
    assert rep.by_barcode == 0 and products(session) == 1


def test_migration_adds_the_column_to_an_old_database(tmp_path):
    url = f"sqlite:///{tmp_path / 'old.db'}"
    engine = make_engine(url)
    migrate(engine)
    with engine.begin() as c:  # turn it back into a version-2 database
        c.execute(text("DROP INDEX IF EXISTS ix_listing_barcode"))
        c.execute(text("ALTER TABLE listing DROP COLUMN barcode"))
        c.execute(text("DELETE FROM schema_version WHERE version = 3"))
        c.execute(text("INSERT INTO store (name) VALUES ('Checkers')"))
        c.execute(
            text(
                "INSERT INTO listing (store_id, ident, name, size_text, url, category, match_status, "
                "match_score, match_reason, first_seen, last_seen) VALUES (1, 'x', 'Milk', '', '', '', "
                "'new', 0, '', '2026-11-01', '2026-11-01')"
            )
        )
    assert migrate(engine) == [3]
    with engine.begin() as c:
        assert c.execute(text("SELECT name, barcode FROM listing")).one() == ("Milk", "")
    assert migrate(engine) == []  # running again changes nothing


def test_rematch_joins_lonely_products_keeps_manual_and_moves_session_items(session, settings):
    from pricepulse.ingest import rematch

    ingest_items(
        session,
        [row("Checkers", "Acme Rice 1kg", 30.0), row("Pick n Pay", "Acme Rice 1kg", 31.0)],
        "paarl",
        T,
        settings,
    )
    # Pretend an older size rule had read the second store's size wrongly, so they were split.
    first, second = session.scalars(select(Listing).order_by(Listing.id)).all()
    a = session.get(Product, first.product_id)
    b = Product(
        name=a.name,
        brand=a.brand,
        brand_key=a.brand_key,
        size_text="1 kg",
        size_key="wrong",
        category=a.category,
        norm_name=a.norm_name,
    )
    session.add(b)
    session.flush()
    second.product_id = b.id
    second.match_status = "new"
    session.flush()
    assert products(session) == 2
    sess = LookupSession(uid=_user(session), day="2026-11-15")
    session.add(sess)
    session.flush()
    session.add(SessionItem(session_id=sess.id, product_id=b.id))
    session.flush()
    rep = rematch(session, settings)
    assert rep.refreshed >= 1 and rep.joined == 1
    assert products(session) == 1
    item = session.scalar(select(SessionItem))
    assert item.product_id == session.scalar(select(Product.id))
    # decisions the owner made are not touched
    lst = session.scalars(select(Listing)).first()
    lst.match_status = "manual"
    assert rematch(session, settings).joined == 0


def test_rematch_rereads_the_matching_words_so_old_products_join_under_new_rules(session, settings):
    from pricepulse.ingest import rematch

    ingest_items(
        session,
        [
            row("Checkers", "Brown Mushrooms 250g", 39.99),
            row("Pick n Pay", "PnP Brown Mushrooms 250g", 39.99),
        ],
        "paarl",
        T,
        settings,
    )
    # Make the two products look as they did under older rules: the words stored on them differ.
    first, second = session.scalars(select(Listing).order_by(Listing.id)).all()
    if first.product_id == second.product_id:  # joined already at ingest (new rules): split them
        old = session.get(Product, first.product_id)
        extra = Product(
            name=old.name,
            size_text="250g",
            size_key=old.size_key,
            unit=old.unit,
            qty=old.qty,
            category=old.category,
        )
        session.add(extra)
        session.flush()
        second.product_id = extra.id
        second.match_status = "new"
    for lst, brand in ((first, ""), (second, "pnp")):
        p = session.get(Product, lst.product_id)
        p.brand_key, p.norm_name, p.is_generic = brand, "brown mushroom " + brand, False
    session.flush()
    assert products(session) == 2
    rep = rematch(session, settings)
    assert rep.joined == 1 and products(session) == 1
    assert rep.refreshed >= 2  # the stored words were re-read


def test_rematch_merges_products_that_already_hold_several_listings(session, settings):
    from pricepulse.db import get_or_create_store
    from pricepulse.ingest import rematch

    def product_with(stores, name):
        p = Product(
            name=name,
            size_text="250g",
            size_key="1x250g",
            unit="g",
            qty=250,
            category="Fresh",
            brand_key="",
            norm_name="brown mushroom",
            is_generic=True,
        )
        session.add(p)
        session.flush()
        for store in stores:
            st = get_or_create_store(session, store)
            session.add(
                Listing(
                    store_id=st.id,
                    ident=f"{store}|{name}",
                    name=name,
                    size_text="250g",
                    product_id=p.id,
                    match_status="new",
                )
            )
        session.flush()
        return p

    product_with(["Checkers", "Woolworths"], "Brown Mushrooms")  # two listings already
    product_with(
        ["Pick n Pay", "Dis-Chem"], "PnP Brown Mushrooms"
    )  # two listings: the single-listing rule cannot reach it
    assert products(session) == 2
    rep = rematch(session, settings)
    assert rep.merged == 1 and products(session) == 1
    assert session.scalar(select(func.count(func.distinct(Listing.store_id)))) == 4


def test_merge_never_joins_products_that_share_a_store_or_were_decided_by_hand(session, settings):
    from pricepulse.ingest import rematch

    rows = [
        row("Checkers", "Brown Mushrooms 250g", 39.99, url="https://c.example/1"),
        row("Checkers", "Brown Mushrooms Pack 250g", 41.0, url="https://c.example/2"),
    ]
    ingest_items(session, rows, "paarl", T, settings)
    before = products(session)
    rematch(session, settings)
    assert products(session) == before  # one store never sells one product twice

    ingest_items(
        session,
        [row("Woolworths", "Brown Mushrooms 250g", 41.99, url="https://w.example/3")],
        "paarl",
        T,
        settings,
    )
    for lst in session.scalars(select(Listing)).all():
        lst.match_status = "manual"
    session.flush()
    n = products(session)
    rematch(session, settings)
    assert products(session) == n  # the owner's answers stay as they are


def test_plain_produce_cards_carry_no_house_brand_label(session, settings):
    from pricepulse.ingest import rematch

    ingest_items(
        session,
        [
            row("Pick n Pay", "PnP Brown Mushrooms 250g", 39.99),
            row("Checkers", "Brown Mushrooms 250g", 39.99),
            row("Pick n Pay", "PnP Full Cream Milk 2L", 30.0),
        ],
        "paarl",
        T,
        settings,
    )
    names = {p.name for p in session.scalars(select(Product)).all()}
    assert "Brown Mushrooms" in names and not any(n.startswith("PnP Brown") for n in names)
    assert any(
        n.startswith("PnP Full Cream Milk") for n in names
    )  # a house-brand milk is a different product
    # an older card that still carries the label is cleaned by rematch
    mush = session.scalars(select(Product).where(Product.name == "Brown Mushrooms")).one()
    mush.name, mush.brand = "PnP Brown Mushrooms", "Pnp"
    session.flush()
    rematch(session, settings)
    assert (
        session.get(Product, mush.id).name == "Brown Mushrooms" and session.get(Product, mush.id).brand == ""
    )
