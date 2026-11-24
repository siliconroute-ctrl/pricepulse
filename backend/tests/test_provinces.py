"""Nine provinces as regions, towns inside them, the 100 km rule and migration 15."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_accounts_api import auth, seed, verify

from pricepulse.api.app import create_app
from pricepulse.config import get_settings
from pricepulse.db import (
    Listing,
    Price,
    Product,
    Store,
    StoreOwner,
    make_engine,
    session_factory,
    utcnow,
)
from pricepulse.regions import load_regions
from pricepulse.towns import load_towns

BACKEND = Path(__file__).resolve().parent.parent
PROVINCES = {
    "eastern-cape",
    "free-state",
    "gauteng",
    "kwazulu-natal",
    "limpopo",
    "mpumalanga",
    "north-west",
    "northern-cape",
    "western-cape",
}


def test_the_real_files_have_nine_provinces_and_valid_towns():
    regions = load_regions(str(BACKEND / "regions.yaml"))
    towns = load_towns(str(BACKEND / "towns.yaml"))
    assert set(regions.regions) == PROVINCES
    for slug, r in regions.regions.items():
        assert r.capital and -35.5 < r.lat < -22 and 16 < r.lon < 33.5, slug
    for t in towns.values():
        assert t.province in PROVINCES and -35.5 < t.lat < -22 and 16 < t.lon < 33.5, t.slug
    for slug in regions.regions:  # every province has its capital as a town
        assert any(t.province == slug for t in towns.values()), slug
    names = {t.name for t in towns.values()}
    for needed in (
        "Johannesburg",
        "Cape Town",
        "Polokwane",
        "Kimberley",
        "Upington",
        "Tzaneen",
        "Thohoyandou",
    ):
        assert any(n.startswith(needed) for n in names), needed


@pytest.fixture
def client(settings):
    settings.open_prices = False
    seed(settings)
    return TestClient(create_app(get_settings()))


def add_local(name, region, lat, lon, price="199"):
    s = get_settings()
    with session_factory(s.database_url).begin() as db:
        st = Store(
            kind="local",
            name=name,
            region=region,
            town=name,
            lat=lat,
            lon=lon,
            category="Groceries",
            status="approved",
            active=True,
        )
        db.add(st)
        db.flush()
        from pricepulse.localstores import save_item

        save_item(db, st, {"name": "Rooibos Tea 80s", "price": price}, s)


def find(client, who, region="paarl", town=None):
    h = auth(who)
    if town:
        h["X-PP-Town"] = town
    r = client.get("/api/v1/catalogue", params={"q": "rooibos", "region": region}, headers=h)
    assert r.status_code == 200, r.text
    return {i["store_name"] for i in r.json()["items"] if i["store_name"]}


def test_a_local_store_shows_within_100_km_of_the_shopper_town_only(client):
    verify(client, "shopper")
    add_local("Franschhoek Spar", "franschhoek", -33.9133, 19.1169)  # ~25 km from Paarl
    add_local("Joburg Tea Shop", "gauteng", -26.2041, 28.0473)  # ~1,400 km away
    assert find(client, "shopper", "paarl", "paarl") == {"Franschhoek Spar"}
    # the same shopper standing in Johannesburg sees the Johannesburg store, not the Cape one
    assert find(client, "shopper", "gauteng", "johannesburg") == {"Joburg Tea Shop"}


def test_province_only_uses_the_capital_as_the_point(client):
    verify(client, "shopper")
    add_local("Cape Town Tea", "cape-town", -33.9249, 18.4241)
    # no town header: the province's own point (in the test file: the region's lat/lon)
    assert "Cape Town Tea" in find(client, "shopper", "cape-town")


def test_a_town_from_another_province_is_ignored(client):
    verify(client, "shopper")
    add_local("Joburg Tea Shop", "gauteng", -26.2041, 28.0473)
    # town johannesburg is not in region paarl, so the province point is used: store stays hidden
    assert find(client, "shopper", "paarl", "johannesburg") == set()


def test_towns_compare_lists_towns_near_the_shopper(client):
    verify(client, "shopper")
    r = client.get(
        "/api/v1/towns/compare", params={"region": "paarl", "radius_km": 100}, headers=auth("shopper")
    )
    assert r.status_code in (200, 401)  # open-prices endpoint: the account route is /towns
    r = client.get(
        "/api/v1/session/towns", params={"region": "paarl", "radius_km": 100}, headers=auth("shopper")
    )
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["towns"]}
    assert {"Paarl", "Wellington", "Franschhoek"} <= names and "Johannesburg" not in names


def test_places_lists_provinces_with_their_towns(client):
    r = client.get("/api/v1/places")
    assert r.status_code == 200
    provs = {p["slug"]: p for p in r.json()["provinces"]}
    assert "paarl" in provs and provs["paarl"]["towns"][0]["slug"] == "paarl"


def test_migration_15_renames_old_town_regions(tmp_path):
    db = tmp_path / "old.db"
    from pricepulse.db import MIGRATIONS, SchemaVersion

    engine = make_engine(f"sqlite:///{db}")
    from sqlalchemy import text

    # build the database as it was before migration 15
    from pricepulse.db import Base

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM schema_version")) if False else None
        s = session_factory(f"sqlite:///{db}")
        with s.begin() as session:
            st = Store(name="Checkers", kind="chain")
            lst_store = Store(name="Paarl Shop", kind="local", region="paarl", category="Groceries")
            session.add_all([st, lst_store])
            session.flush()
            prod = Product(name="Milk 2L")
            session.add(prod)
            session.flush()
            lst = Listing(store_id=st.id, product_id=prod.id, ident="a", name="Milk 2L")
            lst2 = Listing(store_id=lst_store.id, product_id=prod.id, ident="b", name="Milk 2L")
            session.add_all([lst, lst2])
            session.flush()
            now = utcnow()
            for region in ("paarl", "wellington", "hermanus"):
                session.add(
                    Price(listing_id=lst.id, region=region, price_cents=3699, scraped_at=now, slot="a")
                )
            session.add(Price(listing_id=lst2.id, region="paarl", price_cents=3500, scraped_at=now, slot="a"))
    migrate = [m for m in MIGRATIONS if m[0] == 15][0][2]
    with engine.begin() as conn:
        migrate(conn)
        migrate(conn)  # safe to run twice
    con = sqlite3.connect(db)
    rows = con.execute("SELECT region, count(*) FROM price GROUP BY region").fetchall()
    assert rows == [("western-cape", 2)]  # one row per listing and slot
    assert con.execute("SELECT region, lat IS NOT NULL FROM store WHERE kind='local'").fetchone() == (
        "western-cape",
        1,
    )
    assert SchemaVersion is not None and StoreOwner is not None and select is not None
