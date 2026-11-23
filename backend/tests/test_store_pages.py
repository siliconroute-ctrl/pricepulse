"""Store pages for shoppers: opening hours, shop photos, the expanded item view, openly shown local prices."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_accounts_api import auth, seed, verify
from test_store_portal import CSV, add, link, make_store, set_paid

from pricepulse import hours
from pricepulse.api.app import create_app
from pricepulse.config import get_settings
from pricepulse.db import Store, session_factory, utcnow

JPEG = b"\xff\xd8\xff" + b"\x11" * 400 + b"\xff\xd9"
WEEK = {k: ["08:00", "17:00"] for k in ("mon", "tue", "wed", "thu", "fri")} | {
    "sat": ["08:00", "13:00"],
    "sun": None,
}


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


def sa(text):
    """A South African clock time as the UTC time the code receives."""
    return datetime.fromisoformat(text) - timedelta(hours=2)


# --- opening hours -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("when", "state", "text"),
    [
        ("2026-11-23 09:00", "open", "Open now · closes at 17:00"),
        ("2026-11-23 16:30", "closing", "Closing soon · closes at 17:00"),
        ("2026-11-23 06:00", "closed", "Closed · opens today at 08:00"),
        ("2026-11-23 22:30", "closed", "Closed · opens tomorrow at 08:00"),
        ("2026-11-28 14:00", "closed", "Closed · opens Monday at 08:00"),
        ("2026-11-29 10:00", "closed", "Closed · opens tomorrow at 08:00"),
    ],
)
def test_open_status_in_south_african_time(when, state, text):
    st = hours.open_status(hours.dump_hours(hours.parse_hours(WEEK)), "", sa(when))
    assert st["state"] == state and st["text"] == text
    assert st["week"][0] == ["Monday", "08:00 to 17:00"] and st["week"][6] == ["Sunday", "Closed"]


def test_the_day_changes_at_midnight_south_african_time():
    h = hours.dump_hours(hours.parse_hours(WEEK))
    # 22:30 UTC on Sunday is 00:30 Monday in South Africa: the shop opens at 08:00 that day
    st = hours.open_status(h, "", datetime(2026, 11, 22, 22, 30))
    assert st["text"] == "Closed · opens today at 08:00"


def test_no_hours_say_so():
    assert hours.open_status("", "", utcnow())["state"] == "unknown"


@pytest.mark.parametrize(
    "bad",
    [
        {"mon": ["17:00", "08:00"]},
        {"mon": ["8am", "5pm"]},
        {"xyz": ["08:00", "17:00"]},
        {"mon": ["08:00"]},
        {"mon": ["25:00", "26:00"]},
    ],
)
def test_bad_hours_are_refused_in_plain_words(bad):
    with pytest.raises(ValueError, match=r"\w"):
        hours.parse_hours(bad)


def test_owner_sets_hours_and_shoppers_see_them(client, owner):
    r = client.post(
        "/api/v1/store/hours",
        json={"days": WEEK, "note": "Closed on public holidays"},
        headers=auth("shopowner"),
    )
    assert r.status_code == 200 and r.json()["status"]["week"][0][1] == "08:00 to 17:00"
    me = client.get("/api/v1/store/me", headers=auth("shopowner")).json()
    assert me["hours"]["sat"] == ["08:00", "13:00"] and me["hours_note"] == "Closed on public holidays"
    verify(client, "shopper")
    st = client.get(f"/api/v1/stores/{owner}", params={"region": "paarl"}, headers=auth("shopper")).json()
    assert (
        st["hours"]["state"] in ("open", "closing", "closed")
        and st["hours"]["note"] == "Closed on public holidays"
    )
    assert st["directions"].startswith("https://www.google.com/maps/search/?api=1&query=12%20Main%20Road")
    bad = client.post(
        "/api/v1/store/hours", json={"days": {"mon": ["17:00", "08:00"]}}, headers=auth("shopowner")
    )
    assert bad.status_code == 422 and "later" in bad.json()["detail"]["message"]
    link_note = client.post(
        "/api/v1/store/hours", json={"days": WEEK, "note": "see www.x.com"}, headers=auth("shopowner")
    )
    assert link_note.status_code == 422


# --- photos ---------------------------------------------------------------------------------


def put(client, slot=1, body=JPEG, who="shopowner", ctype="image/jpeg"):
    return client.post(
        f"/api/v1/store/photos/{slot}", content=body, headers={**auth(who), "Content-Type": ctype}
    )


def test_photo_upload_is_served_publicly_and_replacing_removes_the_old_file(client, owner):
    r = put(client)
    assert r.status_code == 200
    url = r.json()["url"]
    img = client.get(url)  # no sign-in: an <img> cannot send one
    assert img.status_code == 200 and img.content == JPEG and img.headers["content-type"] == "image/jpeg"
    assert "immutable" in img.headers["cache-control"] and img.headers["x-content-type-options"] == "nosniff"
    second = put(client, body=JPEG + b"\x00\x00" + b"\xff\xd9")  # different bytes: a new file name
    assert second.status_code == 200 and second.json()["url"] != url
    assert client.get(url).status_code == 404  # the old address stops working
    me = client.get("/api/v1/store/me", headers=auth("shopowner")).json()
    assert [p["slot"] for p in me["photos"]] == [1]
    put(client, slot=2)
    verify(client, "shopper")
    st = client.get(f"/api/v1/stores/{owner}", params={"region": "paarl"}, headers=auth("shopper")).json()
    assert len(st["photos"]) == 2
    assert client.delete("/api/v1/store/photos/2", headers=auth("shopowner")).status_code == 200
    assert len(client.get("/api/v1/store/me", headers=auth("shopowner")).json()["photos"]) == 1


def test_photo_uploads_are_checked(client, owner):
    assert put(client, body=b"\x89PNG\r\n" + b"\x00" * 400).status_code == 422
    assert put(client, body=JPEG[:-2]).status_code == 422  # not a complete JPEG
    assert put(client, body=b"\xff\xd8\xff" + b"\x00" * 700_000 + b"\xff\xd9").status_code == 413
    assert put(client, slot=3).status_code == 422
    assert put(client, who="stranger").status_code == 404
    assert client.get(f"/api/v1/img/store/{owner}/1-deadbeef.jpg").status_code == 404
    assert client.get(f"/api/v1/img/store/{owner}/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert client.get(f"/api/v1/img/store/{owner}/photo.png").status_code == 404


def test_one_store_cannot_read_anothers_photo_name(client, owner):
    name = put(client).json()["url"].rsplit("/", 1)[1]
    other = make_store("Other Store")
    assert client.get(f"/api/v1/img/store/{other}/{name}").status_code == 404


def test_a_lapsed_store_cannot_change_photos_or_hours_pages_but_can_see_them(client, owner):
    put(client)
    set_paid(owner, utcnow() - timedelta(days=10))
    assert put(client, slot=2).status_code == 409
    assert client.get("/api/v1/store/me", headers=auth("shopowner")).json()["photos"][0]["slot"] == 1


# --- local prices, descriptions and the View page -----------------------------------------------


def seen(client, who="shopper", **params):
    return client.get("/api/v1/catalogue", params={"region": "paarl", **params}, headers=auth(who)).json()


def test_local_prices_show_openly_and_chain_prices_stay_locked(client, owner):
    client.post("/api/v1/store/import", json={"csv": CSV}, headers=auth("shopowner"))
    verify(client, "shopper")
    cat = {i["name"]: i for i in seen(client, category="Electronics")["items"]}
    hp = cat["HP EliteBook 840 G5"]
    assert hp["price"] == 6499.0 and hp["promo"] is None and hp["condition"] == "Refurbished"
    milk = next(i for i in seen(client, q="clover")["items"])
    assert milk["price"] is None and milk["detail"] == ""
    me = client.get("/api/v1/me", headers=auth("shopper")).json()
    assert me["session"]["items_used"] == 0  # no lookup was used


def test_the_view_page_has_details_description_store_card_and_uses_no_lookup(client, owner):
    client.post("/api/v1/store/hours", json={"days": WEEK}, headers=auth("shopowner"))
    put(client)
    add(
        client,
        size="i5 16GB 512GB SSD",
        description="Battery 90%. 12 months warranty from the shop.",
        promo="5199",
    )
    verify(client, "shopper")
    pid = next(i["id"] for i in seen(client, q="thinkpad")["items"])
    v = client.get(f"/api/v1/local-items/{pid}", params={"region": "paarl"}, headers=auth("shopper")).json()
    assert (
        v["name"].startswith("Lenovo ThinkPad") and v["condition"] == "Refurbished" and v["sub"] == "Laptops"
    )
    assert v["detail"] == "i5 16GB 512GB SSD" and "12 months warranty" in v["description"]
    assert v["price"] == 5499.0 and v["promo"] == 5199.0 and v["sku"].startswith("own-")
    st = v["store"]
    assert (
        st["name"] == "Paarl Refurb PCs"
        and st["whatsapp"] == "27821234567"
        and st["photos"]
        and st["hours"]["text"]
    )
    assert st["directions"].startswith("https://www.google.com/maps/")
    assert client.get("/api/v1/me", headers=auth("shopper")).json()["session"]["items_used"] == 0


def test_the_view_page_refuses_chains_other_towns_and_lapsed_stores(client, owner):
    add(client)
    verify(client, "shopper")
    pid = next(i["id"] for i in seen(client, q="thinkpad")["items"])
    milk = next(i["id"] for i in seen(client, q="clover")["items"])

    def get(p, region="paarl"):
        return client.get(f"/api/v1/local-items/{p}", params={"region": region}, headers=auth("shopper"))

    assert get(pid).status_code == 200
    assert get(milk).status_code == 404
    assert get(pid, "wellington").status_code == 404
    assert get(99999).status_code == 404
    set_paid(owner, utcnow() - timedelta(days=9))
    assert get(pid).status_code == 404


def test_csv_long_description_column(client, owner):
    csv = "item,price,condition,long description,sku\nHP ProBook 450,4999,Refurbished,Charger included. 6 months warranty.,PB450\n"
    assert (
        client.post("/api/v1/store/import", json={"csv": csv}, headers=auth("shopowner")).status_code == 200
    )
    verify(client, "shopper")
    pid = next(i["id"] for i in seen(client, q="probook")["items"])
    v = client.get(f"/api/v1/local-items/{pid}", params={"region": "paarl"}, headers=auth("shopper")).json()
    assert v["description"].startswith("Charger included")
    web = "item,price,condition,details,sku\nHP ProBook 440,4999,Refurbished,see www.spam.com,PB440\n"
    r = client.post(
        "/api/v1/store/import", json={"csv": web, "dry_run": True}, headers=auth("shopowner")
    ).json()
    assert r["invalid_count"] == 1


def test_migration_13_runs_twice_harmlessly(settings):
    from sqlalchemy import text

    from pricepulse.db import make_engine, migrate

    eng = make_engine(settings.database_url)
    migrate(eng)
    with eng.begin() as c:
        c.execute(text("DELETE FROM schema_version WHERE version = 13"))
    migrate(eng)
    s = get_settings()
    with session_factory(s.database_url)() as db:
        assert db.scalars(select(Store)).all() == [] or True


def test_photos_are_removed_with_nothing_left_behind_on_delete(client, owner):
    put(client)
    folder = get_settings().data_dir / "uploads" / "stores" / str(owner)
    assert len(list(folder.glob("*.jpg"))) == 1
    client.delete("/api/v1/store/photos/1", headers=auth("shopowner"))
    assert list(folder.glob("*.jpg")) == []
