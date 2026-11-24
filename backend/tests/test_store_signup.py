"""Store-owner self sign-up: apply, wait for approval, Store ID, 100 km, liquor rules."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_accounts_api import MINOR, auth, seed, verify

from pricepulse.api.app import create_app
from pricepulse.config import get_settings
from pricepulse.db import AppUser, Store, session_factory
from pricepulse.storeid import make_store_id, valid_store_id

APPLY = {
    "name": "Pets Corner",
    "category": "Pets",
    "town": "paarl",
    "address": "5 Church Street",
    "phone": "082 123 4567",
    "whatsapp": "",
    "contact_name": "Sam",
    "email": "sam@example.com",
    "licence_no": "",
    "accept_terms": True,
}
ITEM = {"name": "Pedigree Adult Dog Food 8kg", "price": "249.99", "in_stock": True}


@pytest.fixture
def client(settings):
    settings.open_prices = False
    seed(settings)
    return TestClient(create_app(get_settings()))


def apply(client, who="sam", **over):
    client.get("/api/v1/me", headers=auth(who))
    return client.post("/api/v1/store/apply", json={**APPLY, **over}, headers=auth(who))


def approve(store_id, date="2099-01-31"):
    s = get_settings()
    from datetime import datetime, timedelta

    with session_factory(s.database_url).begin() as db:
        st = db.get(Store, store_id)
        st.status, st.active = "approved", True
        st.paid_until = datetime.strptime(date, "%Y-%m-%d") + timedelta(hours=23)


def store_id_of(name):
    s = get_settings()
    with session_factory(s.database_url)() as db:
        return db.scalar(select(Store.id).where(Store.name == name))


def test_store_ids_have_13_digits_and_a_check_digit():
    for _ in range(50):
        n = make_store_id()
        assert len(n) == 13 and n.isdigit() and n[0] != "0" and valid_store_id(n)
    n = make_store_id()
    bad = n[:-1] + str((int(n[-1]) + 1) % 10)
    assert not valid_store_id(bad) and not valid_store_id("123")


def test_apply_creates_a_pending_hidden_store_with_a_store_id(client):
    r = apply(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending" and valid_store_id(body["store_number"])
    me = client.get("/api/v1/store/me", headers=auth("sam")).json()
    assert me["status"] == "pending" and me["store_number"] == body["store_number"]
    assert me["max_items"] == 200 and me["category"] == "Pets"
    assert client.get("/api/v1/me", headers=auth("sam")).json()["store_owner"] is True
    # a second application from the same account is refused
    again = client.post("/api/v1/store/apply", json={**APPLY, "name": "Other"}, headers=auth("sam"))
    assert again.status_code == 409


def test_a_pending_store_can_add_items_but_shoppers_see_nothing(client):
    apply(client)
    r = client.post("/api/v1/store/items", json=ITEM, headers=auth("sam"))
    assert r.status_code == 200 and r.json()["items"] == 1
    verify(client, "shopper")
    cat = client.get("/api/v1/catalogue", params={"q": "pedigree"}, headers=auth("shopper")).json()
    assert cat["items"] == []
    sid = store_id_of("Pets Corner")
    assert (
        client.get(f"/api/v1/stores/{sid}", params={"region": "paarl"}, headers=auth("shopper")).status_code
        == 404
    )
    approve(sid)
    cat = client.get("/api/v1/catalogue", params={"q": "pedigree"}, headers=auth("shopper")).json()
    assert [i["category"] for i in cat["items"]] == ["Pets"]
    assert (
        client.get(f"/api/v1/stores/{sid}", params={"region": "paarl"}, headers=auth("shopper")).status_code
        == 200
    )


def test_bad_applications_are_explained(client):
    assert apply(client, "a1", accept_terms=False).status_code == 422
    assert apply(client, "a2", category="Cars").json()["detail"]["code"] == "bad_category"
    assert apply(client, "a3", town="atlantis").json()["detail"]["code"] == "bad_town"
    assert apply(client, "a4", phone="12").json()["detail"]["code"] == "bad_phone"
    assert apply(client, "a5", name="Visit www.spam.co").json()["detail"]["code"] == "bad_name"
    assert apply(client, "a6", address="see http://x.co").json()["detail"]["code"] == "bad_details"


def test_liquor_needs_a_licence_and_an_adult(client):
    assert apply(client, "l1", name="Wine Shop", category="Liquor").json()["detail"]["code"] == "licence"
    r = apply(client, "l2", name="Wine Shop", category="Liquor", licence_no="WC/123")
    assert r.json()["detail"]["code"] == "age_check_needed"  # no age check yet
    verify(client, "l3", id_number=MINOR)
    r = apply(client, "l3", name="Wine Shop", category="Liquor", licence_no="WC/123")
    assert r.json()["detail"]["code"] == "age_check_needed"
    verify(client, "l4")
    r = apply(client, "l4", name="Wine Shop", category="Liquor", licence_no="WC/123")
    assert r.status_code == 200


def test_a_liquor_store_page_is_hidden_from_minors(client):
    verify(client, "adult")
    apply(client, "adult", name="Wine Shop", category="Liquor", licence_no="WC/123")
    sid = store_id_of("Wine Shop")
    approve(sid)
    verify(client, "kid", id_number=MINOR)
    assert (
        client.get(f"/api/v1/stores/{sid}", params={"region": "paarl"}, headers=auth("kid")).status_code
        == 404
    )
    assert (
        client.get(f"/api/v1/stores/{sid}", params={"region": "paarl"}, headers=auth("adult")).status_code
        == 200
    )


def test_a_store_is_shown_within_100_km_and_not_beyond(client):
    apply(client, "far", name="Joburg Pets", town="johannesburg")
    apply(client, "near", name="Franschhoek Pets", town="franschhoek")
    for who in ("far", "near"):
        assert client.post("/api/v1/store/items", json=ITEM, headers=auth(who)).status_code == 200
    approve(store_id_of("Joburg Pets"))
    approve(store_id_of("Franschhoek Pets"))
    verify(client, "shopper")
    stores = {
        i["store_name"]
        for i in client.get("/api/v1/catalogue", params={"q": "pedigree"}, headers=auth("shopper")).json()[
            "items"
        ]
    }
    assert stores == {"Franschhoek Pets"}


def test_admin_can_approve_and_reject(client):
    apply(client)
    sid = store_id_of("Pets Corner")
    s = get_settings()
    with session_factory(s.database_url).begin() as db:
        db.scalars(select(AppUser)).first().is_admin = True
        uid = db.scalars(select(AppUser)).first().uid
    admin = (
        {"Authorization": f"Bearer dev:{uid.removeprefix('dev-')}"} if uid.startswith("dev-") else auth("sam")
    )
    r = client.post(
        f"/api/v1/admin/localstores/{sid}/approve", json={"paid_until": "2099-01-31"}, headers=admin
    )
    assert r.status_code == 200 and r.json()["status_flag"] == "approved"
    r = client.post(f"/api/v1/admin/localstores/{sid}/reject", json={"reason": "No licence"}, headers=admin)
    assert r.json()["status_flag"] == "rejected" and r.json()["active"] is False
    me = client.get("/api/v1/store/me", headers=auth("sam")).json()
    assert me["status"] == "rejected" and me["reject_reason"] == "No licence"
    assert client.post("/api/v1/store/items", json=ITEM, headers=auth("sam")).status_code == 409


def test_migration_14_gives_existing_local_stores_a_store_id(client):
    s = get_settings()
    with session_factory(s.database_url).begin() as db:
        db.add(Store(kind="local", name="Old Shop", region="paarl", category="Groceries"))
    from pricepulse.localstores import ensure_store_number

    with session_factory(s.database_url).begin() as db:
        st = db.scalar(select(Store).where(Store.name == "Old Shop"))
        assert valid_store_id(ensure_store_number(db, st))
