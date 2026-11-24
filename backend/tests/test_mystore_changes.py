"""My store changes: operating mode, bulk remove, paging, and the admin's delete-store."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_accounts_api import auth, seed, verify
from test_store_portal import add, link, make_store

from pricepulse.api.app import create_app
from pricepulse.config import get_settings
from pricepulse.db import AppUser, Listing, Price, Product, Store, StoreOwner, session_factory, utcnow


@pytest.fixture
def client(settings):
    settings.open_prices = False
    seed(settings)
    return TestClient(create_app(get_settings()))


@pytest.fixture
def owner(client):
    sid = make_store()
    link(client, "shopowner", sid)
    return sid


def test_a_store_has_a_mode_and_the_owner_can_change_it(client, owner):
    me = client.get("/api/v1/store/me", headers=auth("shopowner")).json()
    assert me["mode"] == "physical"
    body = {"address": "", "phone": "082 123 4567", "mode": "online"}
    assert client.post("/api/v1/store/details", json=body, headers=auth("shopowner")).status_code == 200
    assert client.get("/api/v1/store/me", headers=auth("shopowner")).json()["mode"] == "online"
    # a physical shop needs an address
    r = client.post(
        "/api/v1/store/details",
        json={"address": "", "phone": "082 123 4567", "mode": "physical"},
        headers=auth("shopowner"),
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "address"
    assert (
        client.post("/api/v1/store/details", json={"mode": "moon"}, headers=auth("shopowner")).status_code
        == 422
    )


def test_an_online_only_store_shows_its_town_but_no_address_hours_or_directions(client, owner):
    add(client)
    s = get_settings()
    with session_factory(s.database_url).begin() as db:
        db.get(Store, owner).mode = "online"
        db.get(Store, owner).hours = '{"mon":["08:00","17:00"]}'
    verify(client, "shopper")
    page = client.get(f"/api/v1/stores/{owner}", params={"region": "paarl"}, headers=auth("shopper")).json()
    assert page["town"] == "Paarl" and page["mode"] == "online"
    assert page["address"] == "" and page["directions"] == "" and page["hours"] == {}
    cat = client.get("/api/v1/catalogue", params={"q": "thinkpad"}, headers=auth("shopper")).json()["items"]
    item = client.get(
        f"/api/v1/local-items/{cat[0]['id']}", params={"region": "paarl"}, headers=auth("shopper")
    )
    st = item.json()["store"]
    assert st["town"] == "Paarl" and st["address"] == "" and st["mode"] == "online"


def test_sign_up_asks_for_the_mode(client):
    client.get("/api/v1/me", headers=auth("sam"))
    base = {
        "name": "Home IT",
        "category": "Electronics",
        "town": "paarl",
        "address": "",
        "phone": "082 123 4567",
        "accept_terms": True,
    }
    r = client.post("/api/v1/store/apply", json={**base, "mode": "physical"}, headers=auth("sam"))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "address"
    r = client.post("/api/v1/store/apply", json={**base, "mode": "online"}, headers=auth("sam"))
    assert r.status_code == 200
    me = client.get("/api/v1/store/me", headers=auth("sam")).json()
    assert me["mode"] == "online" and me["town"] == "Paarl"


def test_items_are_paged_and_can_be_removed_in_bulk_or_all_at_once(client, owner):
    for i in range(30):
        assert add(client, name=f"Laptop number {i:02d} thing", sku=f"L{i}").status_code == 200
    first = client.get("/api/v1/store/items", headers=auth("shopowner")).json()
    assert first["total"] == 30 and len(first["items"]) == 25
    second = client.get("/api/v1/store/items", params={"offset": 25}, headers=auth("shopowner")).json()
    assert len(second["items"]) == 5
    ids = [i["id"] for i in first["items"][:3]]
    r = client.post("/api/v1/store/items/remove", json={"ids": ids}, headers=auth("shopowner"))
    assert r.json() == {"removed": 3, "items": 27}
    assert (
        client.post("/api/v1/store/items/remove", json={"ids": []}, headers=auth("shopowner")).status_code
        == 422
    )
    r = client.post("/api/v1/store/items/remove-all", headers=auth("shopowner"))
    assert r.json()["removed"] == 27
    assert client.get("/api/v1/store/me", headers=auth("shopowner")).json()["items"] == 0
    # history kept: the items are still there, hidden
    s = get_settings()
    with session_factory(s.database_url)() as db:
        assert (
            db.scalar(select(Listing).where(Listing.store_id == owner, Listing.active.is_(False))) is not None
        )
    # another store's owner cannot remove these ids
    other = make_store("Other Shop")
    link(client, "other", other)
    r = client.post("/api/v1/store/items/remove", json={"ids": ids}, headers=auth("other"))
    assert r.json()["removed"] == 0


def make_admin(client):
    client.get("/api/v1/me", headers=auth("boss"))
    s = get_settings()
    with session_factory(s.database_url).begin() as db:
        db.scalars(select(AppUser).where(AppUser.uid == "dev-boss")).one().is_admin = True


def test_admin_deletes_a_test_store_and_everything_in_it(client, owner):
    add(client)
    make_admin(client)
    h = auth("boss")
    r = client.post(f"/api/v1/admin/localstores/{owner}/delete", json={"confirm": "wrong"}, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "confirm"
    s = get_settings()
    with session_factory(s.database_url).begin() as db:
        db.get(Store, owner).paid_until = utcnow() + timedelta(days=10)
    r = client.post(
        f"/api/v1/admin/localstores/{owner}/delete", json={"confirm": "Paarl Refurb PCs"}, headers=h
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "paid"
    with session_factory(s.database_url).begin() as db:
        db.get(Store, owner).paid_until = None
    r = client.post(
        f"/api/v1/admin/localstores/{owner}/delete", json={"confirm": "paarl refurb pcs"}, headers=h
    )
    assert r.status_code == 200 and r.json()["deleted"] == "Paarl Refurb PCs"
    with session_factory(s.database_url)() as db:
        assert db.get(Store, owner) is None
        assert db.scalars(select(Listing).where(Listing.store_id == owner)).all() == []
        assert db.scalars(select(StoreOwner).where(StoreOwner.store_id == owner)).all() == []
        assert not [p for p in db.scalars(select(Product)) if "ThinkPad" in p.name]
        assert (
            db.scalar(
                select(Price)
                .join(Listing, Listing.id == Price.listing_id, isouter=True)
                .where(Listing.id.is_(None))
            )
            is None
        )
    # chain stores are untouched and a non-admin gets 404
    assert (
        client.post(
            "/api/v1/admin/localstores/1/delete", json={"confirm": "x"}, headers=auth("shopper")
        ).status_code
        == 404
    )
