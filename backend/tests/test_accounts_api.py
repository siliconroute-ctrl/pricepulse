"""The signed-in app: age check, lookup sessions and limits, liquor, export, privacy."""

import pytest
from conftest import make_id, row
from fastapi.testclient import TestClient
from sqlalchemy import select

from pricepulse.api.app import create_app
from pricepulse.db import AppUser, LookupSession, Subscription, session_factory, utcnow
from pricepulse.ingest import ingest_items

ADULT, MINOR = make_id("800101"), make_id("150315")


@pytest.fixture
def app_settings(settings):
    settings.open_prices = False  # how the real app runs
    return settings


@pytest.fixture
def client(app_settings):
    seed(app_settings)
    return TestClient(create_app(app_settings))


def seed(settings, n_extra=0):
    rows = [
        row("Checkers", "Clover Full Cream Milk 2L", 36.99, category="Fresh"),
        row("Pick n Pay", "Clover Full Cream Milk 2L", 38.49, category="Fresh"),
        row("Woolworths", "Clover Full Cream Milk 2L", 41.99, category="Fresh"),
        row("Checkers", "Albany Superior White Bread 700g", 19.99),
        row("Pick n Pay", "Albany Superior White Bread 700g", 20.99),
        row("Checkers", "Castle Lite 24 x 340ml", 344.99, category="Liquor"),
        row("Ultra Liquors", "Castle Lite 24 x 340ml", 329.99, category="Liquor"),
    ] + [row("Checkers", f"Brand{i} Thing {i} 1L", 10 + i) for i in range(n_extra)]
    Session = session_factory(settings.database_url)
    with Session.begin() as s:
        ingest_items(s, rows, "paarl", utcnow(), settings)


def auth(name="alice"):
    return {"Authorization": f"Bearer dev:{name}"}


def verify(client, name="alice", id_number=ADULT):
    return client.post(
        "/api/v1/me/verify-id", json={"id_number": id_number, "accept_terms": True}, headers=auth(name)
    )


def ids(client, name="alice", **params):
    r = client.get("/api/v1/catalogue", params=params, headers=auth(name)).json()
    return {i["name"]: i["id"] for i in r["items"]}


def add(client, pid, name="alice"):
    return client.post(
        "/api/v1/session/items", json={"product_id": pid, "region": "paarl"}, headers=auth(name)
    )


# --- sign-in and locked prices ---------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/api/v1/me", "/api/v1/catalogue", "/api/v1/session", "/api/v1/session/towns"]
)
def test_sign_in_required(client, path):
    r = client.get(path)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "sign_in"
    assert client.get(path, headers={"Authorization": "Bearer nonsense"}).status_code == 401


@pytest.mark.parametrize(
    "path",
    ["/api/v1/products", "/api/v1/products/1", "/api/v1/towns/compare", "/api/v1/prices", "/api/prices"],
)
def test_whole_catalogue_prices_are_locked(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers=auth()).status_code == 401
    assert (
        client.post(
            "/api/v1/basket/optimise", json={"region": "paarl", "items": [{"product_id": 1}]}
        ).status_code
        == 401
    )


def test_catalogue_has_no_prices(client):
    verify(client)
    body = client.get("/api/v1/catalogue", params={"q": "milk"}, headers=auth()).json()
    assert body["total"] == 1 and body["items"][0]["store_count"] == 3
    assert "price" not in str(body).lower()


def test_private_answers_are_not_cached(client):
    r = client.get("/api/v1/me", headers=auth())
    assert r.headers["cache-control"] == "private, no-store"


# --- age check -------------------------------------------------------------------


def test_new_user_must_check_id_before_lookup(client):
    me = client.get("/api/v1/me", headers=auth()).json()
    assert me["id_checked"] is False and me["plan"] == "free" and me["sessions_per_day"] == 3
    assert me["session"] == {"active": False, "items_used": 0, "items_max": 45}
    pid = ids(client)["Clover Full Cream Milk"]
    r = add(client, pid)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "id_required"


def test_verify_id_and_only_fingerprint_is_stored(client, app_settings):
    r = verify(client)
    assert r.status_code == 200 and r.json()["id_checked"] and r.json()["adult"]
    with session_factory(app_settings.database_url)() as s:
        user = s.scalars(select(AppUser)).one()
        assert ADULT not in str(vars(user)) and len(user.id_fingerprint) == 64
        assert user.adult is True and user.adult_on is None


def test_bad_id_and_terms(client):
    r = client.post(
        "/api/v1/me/verify-id", json={"id_number": "8001015009086", "accept_terms": True}, headers=auth()
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "bad_id"
    r = client.post("/api/v1/me/verify-id", json={"id_number": ADULT, "accept_terms": False}, headers=auth())
    assert r.status_code == 422 and r.json()["detail"]["code"] == "terms"


def test_one_id_one_account(client):
    assert verify(client, "alice").status_code == 200
    r = verify(client, "bob")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "id_in_use"
    assert verify(client, "alice").status_code == 200  # same person again is fine
    r = verify(client, "alice", make_id("790202"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "id_differs"


def test_id_check_off_without_secret(client, app_settings):
    app_settings.id_secret = ""
    assert verify(client).status_code == 503


def test_under_18_never_sees_liquor(client):
    assert verify(client, "kid", MINOR).json()["adult"] is False
    assert "Castle Lite" not in ids(client, "kid")
    assert ids(client, "kid", category="Liquor") == {}
    verify(client, "alice")
    beer = ids(client, "alice")["Castle Lite"]
    r = add(client, beer, "kid")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "adult_only"
    assert add(client, beer, "alice").status_code == 200


# --- sessions and limits -----------------------------------------------------------


def test_lookup_shows_prices_and_basket(client):
    verify(client)
    p = ids(client)
    body = add(client, p["Clover Full Cream Milk"]).json()
    assert body["me"]["session"] == {"active": True, "items_used": 1, "items_max": 45}
    assert body["me"]["sessions_used_today"] == 1
    item = body["items"][0]
    assert item["product"]["best_store"] == "Checkers" and len(item["product"]["prices"]) == 3
    add(client, p["Albany Superior White Bread"])
    body = client.patch(
        f"/api/v1/session/items/{p['Clover Full Cream Milk']}",
        json={"region": "paarl", "quantity": 2},
        headers=auth(),
    ).json()
    assert body["basket"]["cheapest_single_store"]["store"] == "Checkers"
    assert body["basket"]["cheapest_single_store"]["total"] == round(36.99 * 2 + 19.99, 2)
    # Same item again does not use another slot.
    assert add(client, p["Clover Full Cream Milk"]).json()["me"]["session"]["items_used"] == 2


def test_removing_from_list_keeps_the_slot(client):
    verify(client)
    p = ids(client)
    add(client, p["Clover Full Cream Milk"])
    body = client.patch(
        f"/api/v1/session/items/{p['Clover Full Cream Milk']}",
        json={"region": "paarl", "on_list": False},
        headers=auth(),
    ).json()
    assert (
        body["me"]["session"]["items_used"] == 1
        and body["items"][0]["on_list"] is False
        and body["basket"] is None
    )


def test_45_items_per_session(app_settings):
    seed(app_settings, n_extra=50)
    client = TestClient(create_app(app_settings))
    verify(client)
    extra = list(ids(client, q="thing", limit=100).values())
    for pid in extra[:45]:
        assert add(client, pid).status_code == 200
    r = add(client, extra[45])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "session_full"
    assert client.get("/api/v1/me", headers=auth()).json()["session"]["items_used"] == 45


def test_three_sessions_a_day_then_blocked(client):
    verify(client)
    pid = ids(client)["Clover Full Cream Milk"]
    for n in (1, 2, 3):
        body = add(client, pid).json()
        assert body["me"]["sessions_used_today"] == n
        cleared = client.post("/api/v1/session/clear", json={"region": "paarl"}, headers=auth()).json()
        assert cleared["items"] == [] and cleared["me"]["session"]["active"] is False
    r = add(client, pid)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "daily_limit"
    assert "tomorrow" in r.json()["detail"]["message"]
    # Another person is not affected.
    verify(client, "bob", make_id("790202"))
    assert add(client, pid, "bob").status_code == 200


def test_limit_resets_next_day(client, app_settings):
    verify(client)
    pid = ids(client)["Clover Full Cream Milk"]
    for _ in range(3):
        add(client, pid)
        client.post("/api/v1/session/clear", json={"region": "paarl"}, headers=auth())
    with session_factory(app_settings.database_url).begin() as s:
        for sess in s.scalars(select(LookupSession)):
            sess.day = "2020-01-01"
    assert add(client, pid).status_code == 200


def test_paid_plan_has_no_daily_limit(client, app_settings):
    verify(client)
    with session_factory(app_settings.database_url).begin() as s:
        s.scalars(select(AppUser)).one().plan_override = "paid"
    pid = ids(client)["Clover Full Cream Milk"]
    for _ in range(5):
        assert add(client, pid).status_code == 200
        client.post("/api/v1/session/clear", json={"region": "paarl"}, headers=auth())
    me = client.get("/api/v1/me", headers=auth()).json()
    assert me["plan"] == "paid" and me["sessions_per_day"] is None and me["session"]["items_max"] == 45


def test_detail_and_towns_only_for_session_items(client):
    verify(client)
    p = ids(client)
    milk, bread = p["Clover Full Cream Milk"], p["Albany Superior White Bread"]
    add(client, milk)
    d = client.get(f"/api/v1/session/items/{milk}", headers=auth())
    assert d.status_code == 200 and d.json()["history"]
    assert client.get(f"/api/v1/session/items/{bread}", headers=auth()).status_code == 404
    t = client.get("/api/v1/session/towns", headers=auth()).json()
    assert t["product_count"] == 1 and next(x for x in t["towns"] if x["region"] == "paarl")["total"] == 36.99


# --- TrolleyUp export ---------------------------------------------------------------


def test_trolleyup_export(client):
    verify(client)
    p = ids(client)
    for name in ("Clover Full Cream Milk", "Albany Superior White Bread", "Castle Lite"):
        add(client, p[name])
    client.patch(
        f"/api/v1/session/items/{p['Clover Full Cream Milk']}",
        json={"region": "paarl", "quantity": 3},
        headers=auth(),
    )
    r = client.get("/api/v1/session/export/trolleyup", headers=auth())
    assert r.status_code == 200 and "pricepulse-for-trolleyup-" in r.headers["content-disposition"]
    lines = r.text.lstrip("﻿").strip().split("\r\n")
    assert lines[0] == "Item,Quantity,Store,Note"
    assert any(
        x.startswith("Clover Full Cream Milk 2L,3,Checkers,PricePulse price R 36.99 on ") for x in lines
    )
    assert any(x.startswith("Castle Lite 24x340ml,1,Ultra Liquors,") for x in lines)
    single = client.get("/api/v1/session/export/trolleyup", params={"mode": "single"}, headers=auth()).text
    assert "Castle Lite 24x340ml,1,Checkers LiquorShop,PricePulse price R 344.99" in single


def test_export_empty_list(client):
    verify(client)
    assert client.get("/api/v1/session/export/trolleyup", headers=auth()).status_code == 404


# --- privacy -----------------------------------------------------------------------


def test_delete_my_data(client, app_settings):
    verify(client)
    add(client, ids(client)["Clover Full Cream Milk"])
    assert client.delete("/api/v1/me", headers=auth()).json() == {"deleted": True}
    with session_factory(app_settings.database_url)() as s:
        assert s.scalars(select(AppUser)).all() == [] and s.scalars(select(LookupSession)).all() == []
    assert client.get("/api/v1/me", headers=auth()).json()["id_checked"] is False
    assert verify(client, "bob").status_code == 200  # the ID is free to use again


def test_cannot_delete_with_active_plan(client, app_settings):
    verify(client)
    with session_factory(app_settings.database_url).begin() as s:
        s.add(Subscription(uid="dev-alice", status="active", paid_until=utcnow()))
    assert client.delete("/api/v1/me", headers=auth()).status_code == 409


def test_short_id_gets_plain_message(client):
    r = client.post(
        "/api/v1/me/verify-id", json={"id_number": "80010150", "accept_terms": True}, headers=auth()
    )
    assert r.status_code == 422 and "13 digits" in r.json()["detail"]["message"]


def test_production_refuses_test_switches(settings):
    settings.production = True
    with pytest.raises(RuntimeError):
        create_app(settings)  # auth_dev and open_prices are on in the test settings
    settings.auth_dev = settings.open_prices = False
    settings.id_secret = ""
    with pytest.raises(RuntimeError):
        create_app(settings)


def test_admin_plan_and_users(client, app_settings, capsys):
    import admin

    verify(client)
    assert admin.main(["plan", "dev-alice", "paid"]) == 0
    assert client.get("/api/v1/me", headers=auth()).json()["plan"] == "paid"
    assert admin.main(["plan", "nobody", "paid"]) == 1
    admin.main(["users"])
    assert "1 accounts. 1 have done the age check" in capsys.readouterr().out
