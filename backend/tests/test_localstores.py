"""Local (private) stores: the weekly CSV, region scoping, replace-the-list, and matching to chains."""

from datetime import timedelta

import pytest
from conftest import make_id, row
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_accounts_api import add, auth, seed, verify

from pricepulse import localstores
from pricepulse.api.app import create_app
from pricepulse.db import AppUser, Listing, Product, Store, StoreSubmission, session_factory, utcnow
from pricepulse.ingest import ingest_items

CSV = (
    "Item Name,Selling Price,Size,Barcode,SKU,Department,Category,Promo Price\r\n"
    "Clover Full Cream Milk,R 34.50,2L,,MILK2,Dairy,Groceries,\r\n"
    'Boerewors,"89,90",1kg,,BW1,Butchery,Fresh,79.90\r\n'
    "Brown Bread,16.00,700g,6001234567890,BR7,Bakery,Groceries,\r\n"
    "Brown Bread,17.00,700g,6001234567890,BR7,Bakery,Groceries,\r\n"
    "Free Cheese,0,500g,,CH1,Dairy,Groceries,\r\n"
    "X,10,,,,,Groceries,\r\n"
    "Sherry,55,750ml,,SH1,Liquor,Electronics,\r\n"
)


def test_csv_is_read_with_forgiving_headers_and_clear_problems():
    rows, problems = localstores.parse_csv(CSV)
    assert not problems and len(rows) == 7 and rows[0]["name"] == "Clover Full Cream Milk"
    semi, _ = localstores.parse_csv("name;price\nMilk 1L;21,99\n")
    assert semi == [{"name": "Milk 1L", "price": "21,99"}]
    none, problems = localstores.parse_csv("size,barcode\n1L,6001\n")
    assert none == [] and "item name" in problems[0]
    assert localstores.parse_csv("")[1] == ["The file is empty."]
    _, p = localstores.parse_csv("item\nMilk\n")
    assert "price" in p[0]


def test_rows_are_checked_one_by_one_and_bad_ones_explained():
    rows, _ = localstores.parse_csv(CSV)
    chk = localstores.check_rows(rows)
    names = [r["name"] for r in chk.ok]
    assert names == ["Clover Full Cream Milk", "Boerewors", "Brown Bread"]  # repeated SKU counted once
    assert chk.duplicates == 1
    why = {i["name"]: i["why"] for i in chk.invalid}
    assert "price must be" in why["Free Cheese"] and "2 to 120" in why["X"] and "category" in why["Sherry"]
    bw = next(r for r in chk.ok if r["name"] == "Boerewors")
    assert bw["price"] == 89.9 and bw["promo_price"] == 79.9 and bw["department"] == "Butchery"
    assert next(r for r in chk.ok if r["name"] == "Clover Full Cream Milk")["price"] == 34.5  # "R 34.50"


@pytest.fixture
def db(settings):
    Session = session_factory(settings.database_url)
    with Session() as s:
        yield s
        s.commit()


def make_store(db, name="Paarl Corner Café", region="paarl"):
    st = Store(name=name, kind="local", region=region, town="Paarl")
    db.add(st)
    db.flush()
    return st


def test_weekly_list_loads_prices_only_for_its_region_and_replaces_the_old_list(db, settings):
    st = make_store(db)
    rows, _ = localstores.parse_csv(CSV)
    rep = localstores.import_for_store(db, st, rows, settings, filename="week1.csv", by="boss")
    assert (rep.added, rep.updated, rep.removed) == (3, 0, 0) and len(rep.invalid) == 3
    assert st.last_submission_at is not None
    lst = db.scalars(select(Listing).where(Listing.store_id == st.id)).all()
    assert {x.sku for x in lst} == {"MILK2", "BW1", "BR7"} and {x.source for x in lst} == {"csv"}
    assert all(x.active and x.hit_rank == 1 for x in lst)  # local items are always shown to shoppers
    from pricepulse.queries import latest_prices

    assert latest_prices(db, "paarl") and not latest_prices(db, "wellington")  # only in its own town

    # next week: milk price changes, bread is gone, a new item arrives
    week2 = "item,price,sku\nClover Full Cream Milk,36.00,MILK2\nOats 1kg,29.00,OAT1\n"
    rows2, _ = localstores.parse_csv(week2)
    rep2 = localstores.import_for_store(db, st, rows2, settings, now=utcnow() + timedelta(days=7))
    assert (rep2.added, rep2.updated, rep2.removed) == (1, 1, 2)
    active = {
        x.sku
        for x in db.scalars(select(Listing).where(Listing.store_id == st.id, Listing.active.is_(True))).all()
    }
    assert active == {"MILK2", "OAT1"}
    assert db.scalar(select(Listing.active).where(Listing.sku == "BR7")) is False  # hidden, kept for history
    assert db.scalar(select(StoreSubmission.id).where(StoreSubmission.store_id == st.id).limit(1))
    assert len(db.scalars(select(StoreSubmission).where(StoreSubmission.store_id == st.id)).all()) == 2

    # "add and update only" never hides anything
    rows3, _ = localstores.parse_csv("item,price,sku\nSugar 2.5kg,60,SU1\n")
    rep3 = localstores.import_for_store(db, st, rows3, settings, replace=False)
    assert rep3.removed == 0 and db.scalar(select(Listing.active).where(Listing.sku == "OAT1")) is True


def test_a_dry_run_keeps_nothing(db, settings):
    st = make_store(db)
    rows, _ = localstores.parse_csv("item,price\nMilk 2L,34\n")
    rep = localstores.import_for_store(db, st, rows, settings, dry_run=True)
    assert rep.added == 1 and rep.dry_run
    assert db.scalar(select(Listing.id).where(Listing.store_id == st.id)) is None
    assert db.scalar(select(StoreSubmission.id)) is None


def test_local_items_land_on_the_same_card_as_the_chains(db, settings):
    now = utcnow()
    ingest_items(
        db,
        [
            row("Checkers", "Clover Full Cream Milk 2L", 40.0),
            row("Woolworths", "Clover Full Cream Milk 2L", 41.0),
        ],
        "paarl",
        now,
        settings,
    )
    st = make_store(db)
    rows, _ = localstores.parse_csv(
        "item,price,size\nClover Full Cream Milk,34.50,2L\nTannie's Rusks,45,500g\n"
    )
    rep = localstores.import_for_store(db, st, rows, settings)
    assert rep.matched_to_chains == 1  # milk joined the chains' card, the rusks got a card of their own
    from pricepulse.queries import latest_prices

    milk = next(p for p in latest_prices(db, "paarl").values() if "Milk" in p.product.name)
    assert {x.store for x in milk.prices} == {"Checkers", "Woolworths", "Paarl Corner Café"}
    assert (
        milk.best.store == "Paarl Corner Café"
        and next(x for x in milk.prices if x.local).store == "Paarl Corner Café"
    )


def test_only_local_stores_with_a_region_can_be_loaded(db, settings):
    chain = Store(name="Checkers", kind="chain")
    db.add(chain)
    db.flush()
    with pytest.raises(ValueError):
        localstores.import_for_store(db, chain, [{"name": "Milk", "price": "30"}], settings)


# --- the admin endpoints -----------------------------------------------------------------------


@pytest.fixture
def app_settings(settings):
    settings.open_prices = False
    return settings


@pytest.fixture
def api(app_settings):
    seed(app_settings)
    client = TestClient(create_app(app_settings))
    verify(client, "boss")
    verify(client, "alice", id_number=make_id("900101"))
    with session_factory(app_settings.database_url).begin() as s:
        s.scalars(select(AppUser).where(AppUser.uid == "dev-boss")).one().is_admin = True
    return client


STORE = {
    "name": "Paarl Corner Café",
    "region": "paarl",
    "town": "Paarl",
    "contact_name": "Tannie Sanet",
    "phone": "021 555 0101",
}


def test_only_admins_reach_local_stores(api):
    assert api.get("/api/v1/admin/localstores", headers=auth("alice")).status_code == 404
    assert api.post("/api/v1/admin/localstores", json=STORE, headers=auth("alice")).status_code == 404


def test_admin_adds_a_store_loads_its_file_and_shoppers_see_it(api):
    h = auth("boss")
    assert {"slug": "paarl", "name": "Paarl"} in api.get("/api/v1/admin/regions", headers=h).json()["regions"]
    made = api.post("/api/v1/admin/localstores", json=STORE, headers=h)
    assert made.status_code == 200 and made.json()["items"] == 0
    sid = made.json()["id"]
    assert api.post("/api/v1/admin/localstores", json=STORE, headers=h).status_code == 422  # same name twice
    assert (
        api.post("/api/v1/admin/localstores", json={**STORE, "name": "Checkers"}, headers=h).status_code
        == 422
    )
    assert (
        api.post(
            "/api/v1/admin/localstores", json={**STORE, "name": "Far Away", "region": "mars"}, headers=h
        ).status_code
        == 422
    )

    bad = api.post(f"/api/v1/admin/localstores/{sid}/import", json={"csv": "size\n1L\n"}, headers=h)
    assert bad.status_code == 422 and "item name" in bad.json()["detail"]["message"]
    preview = api.post(
        f"/api/v1/admin/localstores/{sid}/import", json={"csv": CSV, "dry_run": True}, headers=h
    ).json()
    assert preview["added"] == 3 and preview["dry_run"] and preview["invalid_count"] == 3
    assert (
        api.get("/api/v1/admin/localstores", headers=h).json()["stores"][0]["items"] == 0
    )  # preview kept nothing
    done = api.post(
        f"/api/v1/admin/localstores/{sid}/import", json={"csv": CSV, "filename": "w1.csv"}, headers=h
    ).json()
    assert done["added"] == 3 and not done["dry_run"]
    row_ = api.get("/api/v1/admin/localstores", headers=h).json()["stores"][0]
    assert row_["items"] == 3 and row_["submissions"] == 1 and row_["last_submission"]
    subs = api.get(f"/api/v1/admin/localstores/{sid}/submissions", headers=h).json()["submissions"]
    assert subs[0]["filename"] == "w1.csv" and subs[0]["added"] == 3 and subs[0]["invalid"] == 3

    # shoppers in Paarl can find and look up the store's items; the price carries the local flag
    cat = api.get("/api/v1/catalogue", params={"q": "boerewors"}, headers=auth("alice")).json()
    assert cat["total"] == 1
    assert add(api, cat["items"][0]["id"], "alice").status_code == 200
    prices = api.get("/api/v1/session", params={"region": "paarl"}, headers=auth("alice")).json()["items"][0][
        "product"
    ]["prices"]
    assert (
        prices[0]["store"] == "Paarl Corner Café"
        and prices[0]["local"] is True
        and prices[0]["stale"] is False
    )
    # a shopper in another town does not see a Paarl store's items
    assert (
        api.get(
            "/api/v1/catalogue", params={"q": "boerewors", "region": "wellington"}, headers=auth("alice")
        ).json()["total"]
        == 0
    )

    # editing and switching the store off hides everything it sold
    upd = api.post(f"/api/v1/admin/localstores/{sid}", json={**STORE, "active": False}, headers=h)
    assert upd.status_code == 200 and upd.json()["active"] is False
    assert api.get("/api/v1/catalogue", params={"q": "boerewors"}, headers=auth("alice")).json()["total"] == 0


def test_a_local_price_is_marked_old_after_eight_days_not_two(api, app_settings):
    h = auth("boss")
    sid = api.post("/api/v1/admin/localstores", json=STORE, headers=h).json()["id"]
    api.post(
        f"/api/v1/admin/localstores/{sid}/import", json={"csv": "item,price\nBoerewors,89.90\n"}, headers=h
    )
    with session_factory(app_settings.database_url).begin() as s:
        from pricepulse.db import Price

        for p in s.scalars(select(Price)).all():
            p.scraped_at = utcnow() - timedelta(days=5)  # five days old: still fine for a weekly store
    pid = api.get("/api/v1/catalogue", params={"q": "boerewors"}, headers=auth("alice")).json()["items"][0][
        "id"
    ]
    add(api, pid, "alice")
    item = api.get("/api/v1/session", headers=auth("alice")).json()["items"][0]["product"]["prices"][0]
    assert item["stale"] is False
    with session_factory(app_settings.database_url).begin() as s:
        for p in s.scalars(select(Price)).all():
            p.scraped_at = utcnow() - timedelta(days=9)
    item = api.get("/api/v1/session", headers=auth("alice")).json()["items"][0]["product"]["prices"][0]
    assert item["stale"] is True


def test_old_databases_get_the_new_columns(tmp_path):
    from sqlalchemy import text

    from pricepulse.db import make_engine, migrate

    engine = make_engine(f"sqlite:///{tmp_path / 'old.db'}")
    migrate(engine)
    with engine.begin() as c:
        for col in (
            "kind",
            "region",
            "town",
            "address",
            "contact_name",
            "phone",
            "email",
            "notes",
            "active",
            "created_at",
            "last_submission_at",
            "api_key_hash",
        ):
            c.execute(text(f"ALTER TABLE store DROP COLUMN {col}"))
        for col in ("sku", "department", "source", "active"):
            c.execute(text(f"ALTER TABLE listing DROP COLUMN {col}"))
        c.execute(text("DROP TABLE store_submission"))
        c.execute(text("DELETE FROM schema_version WHERE version = 8"))
        c.execute(text("INSERT INTO store (name) VALUES ('Checkers')"))
    assert migrate(engine) == [8]
    with engine.begin() as c:
        assert c.execute(text("SELECT kind, region, active FROM store")).one() == ("chain", "", 1)
        c.execute(text("SELECT sku, department, source, active FROM listing"))
        c.execute(text("SELECT count(*) FROM store_submission"))
    assert migrate(engine) == []
    assert Product  # imported for completeness
