"""The admin area: only admins get in, and what they see is limited to what the area is for."""

import pytest
from conftest import make_id
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_accounts_api import ADULT, auth, seed, verify

from admin import main as admin_main
from pricepulse.api.app import create_app
from pricepulse.db import AppUser, session_factory

ADMIN_PATHS = ["overview", "items", "users", "requests", "settings"]


@pytest.fixture
def app_settings(settings):
    settings.open_prices = False  # how the real app runs
    return settings


@pytest.fixture
def api(app_settings):
    seed(app_settings)
    client = TestClient(create_app(app_settings))
    verify(client, "boss")
    verify(client, "alice", id_number=make_id("900101"))
    return client


def make_admin(settings, name="boss"):
    with session_factory(settings.database_url).begin() as s:
        user = s.scalars(select(AppUser).where(AppUser.uid == f"dev-{name}")).one()
        user.is_admin = True


@pytest.mark.parametrize("path", ADMIN_PATHS)
def test_admin_pages_need_sign_in_and_the_admin_flag(api, path):
    assert api.get(f"/api/v1/admin/{path}").status_code == 401
    assert (
        api.get(f"/api/v1/admin/{path}", headers=auth("alice")).status_code == 404
    )  # looks like it is not there


def test_me_says_whether_you_are_an_admin(api, app_settings):
    assert api.get("/api/v1/me", headers=auth("boss")).json()["is_admin"] is False
    make_admin(app_settings)
    assert api.get("/api/v1/me", headers=auth("boss")).json()["is_admin"] is True
    assert api.get("/api/v1/me", headers=auth("alice")).json()["is_admin"] is False


def test_items_list_filters_by_store_and_type(api, app_settings):
    make_admin(app_settings)
    h = auth("boss")
    allr = api.get("/api/v1/admin/items", headers=h).json()
    assert len(allr["items"]) == 7 and not allr["truncated"]
    assert set(allr["stores"]) >= {"Checkers", "Pick n Pay", "Woolworths", "Ultra Liquors"}
    assert "Liquor" in allr["categories"]
    one = api.get("/api/v1/admin/items", params={"store": "Pick n Pay"}, headers=h).json()["items"]
    assert [i["store"] for i in one] == ["Pick n Pay"] * 2
    liq = api.get("/api/v1/admin/items", params={"category": "Liquor"}, headers=h).json()["items"]
    assert {i["name"] for i in liq} == {"Castle Lite 24 x 340ml"} and len(liq) == 2  # admins see liquor
    both = api.get(
        "/api/v1/admin/items", params={"store": "Checkers", "category": "Liquor"}, headers=h
    ).json()
    assert len(both["items"]) == 1 and both["items"][0]["price"] == 344.99
    found = api.get("/api/v1/admin/items", params={"q": "albany"}, headers=h).json()["items"]
    assert len(found) == 2
    names = [(i["store"], i["name"]) for i in allr["items"]]
    assert names == sorted(names)  # by store, then name: easy to read as a printed list


def test_users_and_plan_changes(api, app_settings):
    make_admin(app_settings)
    h = auth("boss")
    users = api.get("/api/v1/admin/users", headers=h).json()
    assert users["total"] == 2
    assert {u["uid"] for u in users["users"]} == {"dev-boss", "dev-alice"}
    assert all(set(u) >= {"uid", "created", "age_checked", "adult", "plan", "admin"} for u in users["users"])
    assert "fingerprint" not in str(users) and ADULT not in str(users)  # no ID numbers, no fingerprints
    assert (
        api.post("/api/v1/admin/users/dev-alice/plan", json={"plan": "tester"}, headers=h).status_code == 200
    )
    me = api.get("/api/v1/me", headers=auth("alice")).json()
    assert me["plan"] == "paid" and me["session"]["items_max"] >= 1000
    assert api.post("/api/v1/admin/users/dev-alice/plan", json={"plan": "free"}, headers=h).status_code == 200
    assert api.get("/api/v1/me", headers=auth("alice")).json()["plan"] == "free"
    assert api.post("/api/v1/admin/users/dev-alice/plan", json={"plan": "god"}, headers=h).status_code == 422
    assert api.post("/api/v1/admin/users/nobody/plan", json={"plan": "paid"}, headers=h).status_code == 404
    # an ordinary account cannot change anything
    r = api.post("/api/v1/admin/users/dev-alice/plan", json={"plan": "tester"}, headers=auth("alice"))
    assert r.status_code == 404


def test_requests_inbox_and_settings(api, app_settings):
    make_admin(app_settings)
    h = auth("boss")
    for who in ("alice", "boss"):
        api.post("/api/v1/requests", json={"term": "White Mushrooms 250g"}, headers=auth(who))
    api.post("/api/v1/requests", json={"term": "oat milk"}, headers=auth("alice"))
    inbox = api.get("/api/v1/admin/requests", headers=h).json()["requests"]
    assert [(r["term"], r["count"]) for r in inbox][0] == ("White Mushrooms 250g", 2)
    assert len(inbox) == 2
    done = api.post(
        "/api/v1/admin/requests/status", json={"term": "white mushrooms 250g", "status": "added"}, headers=h
    )
    assert done.json()["changed"] == 2
    assert len(api.get("/api/v1/admin/requests", headers=h).json()["requests"]) == 1
    added = api.get("/api/v1/admin/requests", params={"status": "added"}, headers=h).json()["requests"]
    assert added[0]["term"] == "White Mushrooms 250g"
    shown = api.get("/api/v1/admin/settings", headers=h).json()["settings"]
    text = str(shown)
    assert "Lookup sessions a day (everyone)" in text and "PP_FREE_SESSIONS" in text
    assert "ingest" not in text.lower() or "off" in text.lower() or "on" in text.lower()
    assert app_settings.id_secret not in text or not app_settings.id_secret  # no secrets in the settings list


def test_overview_counts(api, app_settings):
    make_admin(app_settings)
    o = api.get("/api/v1/admin/overview", headers=auth("boss")).json()
    assert o["accounts"] == 2 and o["age_checked"] == 2 and o["admins"] == 1
    assert o["listings"] == 7 and o["products"] >= 3
    assert {s["store"] for s in o["stores"]} >= {"Checkers", "Pick n Pay"}


def test_admin_role_command(api, app_settings, capsys):
    assert admin_main(["admin-role", "dev-alice", "on"]) == 0
    assert "ON" in capsys.readouterr().out
    assert api.get("/api/v1/admin/overview", headers=auth("alice")).status_code == 200
    assert admin_main(["admin-role", "dev-alice", "off"]) == 0
    assert api.get("/api/v1/admin/overview", headers=auth("alice")).status_code == 404
    assert admin_main(["admin-role", "nobody", "on"]) == 1
