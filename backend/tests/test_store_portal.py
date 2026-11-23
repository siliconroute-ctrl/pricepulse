"""The store-owner portal: one item at a time, CSV, limits, paid-until, shoppers' store page."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_accounts_api import ADULT, auth, seed, verify

from pricepulse import localstores
from pricepulse.api.app import create_app
from pricepulse.config import get_settings
from pricepulse.db import AppUser, Listing, Store, StoreOwner, session_factory, utcnow

ITEM = {
    "name": "Lenovo ThinkPad T480 14in i5 16GB 512GB",
    "price": "5499",
    "condition": "Refurbished",
    "department": "Laptops",
    "in_stock": True,
}


@pytest.fixture
def client(settings):
    settings.open_prices = False
    seed(settings)
    return TestClient(create_app(get_settings()))


def make_store(name="Paarl Refurb PCs", **kw) -> int:
    s = get_settings()
    with session_factory(s.database_url).begin() as db:
        st = Store(
            kind="local",
            name=name,
            region="paarl",
            town="Paarl",
            category="Electronics",
            max_items=kw.pop("max_items", 200),
            store_type="IT & Electronics",
            phone="082 123 4567",
            address="12 Main Road, Paarl",
            **kw,
        )
        db.add(st)
        db.flush()
        return st.id


def link(client, who, store_id):
    client.get("/api/v1/me", headers=auth(who))  # creates the account
    s = get_settings()
    with session_factory(s.database_url).begin() as db:
        uid = db.scalars(select(AppUser).order_by(AppUser.created_at.desc())).first().uid
        db.add(StoreOwner(uid=uid, store_id=store_id))


@pytest.fixture
def owner(client):
    sid = make_store()
    link(client, "shopowner", sid)
    return sid


def add(client, **over):
    return client.post("/api/v1/store/items", json={**ITEM, **over}, headers=auth("shopowner"))


# --- who gets in -----------------------------------------------------------------------


def test_only_the_linked_account_reaches_the_portal(client, owner):
    verify(client, "shopper")
    for path in ("/api/v1/store/me", "/api/v1/store/items", "/api/v1/store/submissions"):
        assert client.get(path, headers=auth("shopper")).status_code == 404
    assert client.post("/api/v1/store/items", json=ITEM, headers=auth("shopper")).status_code == 404
    assert client.get("/api/v1/store/me").status_code == 401
    me = client.get("/api/v1/store/me", headers=auth("shopowner")).json()
    assert me["name"] == "Paarl Refurb PCs" and me["max_items"] == 200 and me["status"] == "active"
    assert "Laptops" in me["subcategories"] and me["conditions"] == ["New", "Refurbished"]
    assert client.get("/api/v1/me", headers=auth("shopowner")).json()["store_owner"] is True
    assert client.get("/api/v1/me", headers=auth("shopper")).json()["store_owner"] is False


# --- one item at a time ----------------------------------------------------------------


def test_add_edit_and_remove_an_item(client, owner):
    r = add(client)
    assert r.status_code == 200 and r.json()["items"] == 1
    item = client.get("/api/v1/store/items", headers=auth("shopowner")).json()["items"][0]
    assert item["sku"].startswith("own-") and item["price"] == 5499.0 and item["condition"] == "Refurbished"
    assert item["sub"] == "Laptops"
    r = client.post(
        f"/api/v1/store/items/{item['id']}",
        json={**ITEM, "price": "5199", "sku": item["sku"]},
        headers=auth("shopowner"),
    )
    assert r.status_code == 200
    items = client.get("/api/v1/store/items", headers=auth("shopowner")).json()["items"]
    assert len(items) == 1 and items[0]["price"] == 5199.0 and items[0]["id"] == item["id"]
    assert client.delete(f"/api/v1/store/items/{item['id']}", headers=auth("shopowner")).json()["items"] == 0
    assert client.get("/api/v1/store/items", headers=auth("shopowner")).json()["items"][0]["active"] is False


@pytest.mark.parametrize(
    ("over", "word"),
    [
        ({"price": "abc"}, "price"),
        ({"price": "0.10"}, "price"),
        ({"name": "x"}, "name"),
        ({"name": "see www.shop.com"}, "web address"),
        ({"condition": ""}, "New or Refurbished"),
        ({"condition": "broken"}, "New or Refurbished"),
    ],
)
def test_bad_items_get_a_plain_message(client, owner, over, word):
    r = add(client, **over)
    assert r.status_code == 422 and word in r.json()["detail"]["message"]


def test_the_category_is_the_stores_own_and_a_phone_is_a_known_subcategory(client, owner):
    add(
        client,
        name="Samsung Galaxy A54 128GB",
        department="Samsung phones",
        condition="New",
        category="Groceries",
    )
    it = client.get("/api/v1/store/items", headers=auth("shopowner")).json()["items"][0]
    assert it["sub"] == "Mobile phones (Samsung / Android)"
    s = get_settings()
    with session_factory(s.database_url)() as db:
        assert (
            db.scalars(select(Listing).where(Listing.name.like("Samsung%"))).one().category == "Electronics"
        )


def test_the_plan_limit_stops_the_201st_item_but_editing_is_allowed(client):
    sid = make_store("Tiny", max_items=10)
    link(client, "shopowner", sid)
    for i in range(10):
        assert add(client, name=f"Dell Latitude {i} refurbished").status_code == 200
    r = add(client, name="Dell Latitude 11 refurbished")
    assert r.status_code == 422 and "10" in r.json()["detail"]["message"]
    first = client.get("/api/v1/store/items", headers=auth("shopowner")).json()["items"][0]
    assert (
        client.post(
            f"/api/v1/store/items/{first['id']}",
            json={**ITEM, "sku": first["sku"]},
            headers=auth("shopowner"),
        ).status_code
        == 200
    )


def test_out_of_stock_hides_an_item_without_deleting_it(client, owner):
    add(client)
    it = client.get("/api/v1/store/items", headers=auth("shopowner")).json()["items"][0]
    client.post(
        f"/api/v1/store/items/{it['id']}",
        json={**ITEM, "sku": it["sku"], "in_stock": False},
        headers=auth("shopowner"),
    )
    assert client.get("/api/v1/store/items", headers=auth("shopowner")).json()["items"][0]["active"] is False
    r = add(client, name="Another one", in_stock=False)
    assert r.status_code == 422


# --- CSV ----------------------------------------------------------------------------------

CSV = (
    "item,price,sub-category,condition,sku\n"
    "HP EliteBook 840 G5,6499,Laptops,Refurbished,HP840\n"
    "Dell OptiPlex 7050 SFF,3999,Desktops,Refurbished,DELL7050\n"
    "Logitech K120 Keyboard,189,Peripherals,New,LOGK120\n"
    "iPhone 11 64GB,5999,iPhones,Refurbished,IP11\n"
    "Bad Row,abc,Laptops,New,BAD\n"
    "No Condition,500,Laptops,,NOC\n"
)


def test_csv_preview_writes_nothing_and_load_adds_without_hiding(client, owner):
    add(client)  # one typed item first
    pre = client.post(
        "/api/v1/store/import", json={"csv": CSV, "dry_run": True}, headers=auth("shopowner")
    ).json()
    assert pre["added"] == 4 and pre["invalid_count"] == 2 and pre["total_after"] == 5
    assert client.get("/api/v1/store/me", headers=auth("shopowner")).json()["items"] == 1  # nothing written
    done = client.post(
        "/api/v1/store/import", json={"csv": CSV, "filename": "stock.csv"}, headers=auth("shopowner")
    ).json()
    assert done["added"] == 4 and done["removed"] == 0
    assert (
        client.get("/api/v1/store/me", headers=auth("shopowner")).json()["items"] == 5
    )  # the typed one stayed
    again = client.post("/api/v1/store/import", json={"csv": CSV}, headers=auth("shopowner")).json()
    assert again["added"] == 0 and again["updated"] == 4
    assert len(client.get("/api/v1/store/submissions", headers=auth("shopowner")).json()["submissions"]) == 2


def test_csv_replace_hides_the_rest_only_when_asked(client, owner):
    add(client)
    r = client.post(
        "/api/v1/store/import", json={"csv": CSV, "replace": True}, headers=auth("shopowner")
    ).json()
    assert r["removed"] == 1
    assert client.get("/api/v1/store/me", headers=auth("shopowner")).json()["items"] == 4


def test_csv_cannot_push_the_store_over_its_limit(client):
    sid = make_store("Small", max_items=10)
    link(client, "shopowner", sid)
    for i in range(8):
        add(client, name=f"Acer Aspire {i} refurbished")
    r = client.post("/api/v1/store/import", json={"csv": CSV}, headers=auth("shopowner"))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "too_many_items"
    assert client.get("/api/v1/store/me", headers=auth("shopowner")).json()["items"] == 8


def test_csv_without_a_price_column_is_refused_in_plain_words(client, owner):
    r = client.post(
        "/api/v1/store/import", json={"csv": "item,price excl vat\nA laptop,100\n"}, headers=auth("shopowner")
    )
    assert r.status_code == 422 and "VAT" in r.json()["detail"]["message"]


# --- what the owner may not change ---------------------------------------------------------


def test_details_can_be_changed_but_not_the_plan_or_name(client, owner):
    r = client.post(
        "/api/v1/store/details",
        json={"address": "5 Church St, Paarl", "phone": "021 555 1234", "whatsapp": "+27 82 111 2222"},
        headers=auth("shopowner"),
    )
    assert r.status_code == 200
    me = client.get("/api/v1/store/me", headers=auth("shopowner")).json()
    assert me["phone"] == "021 555 1234" and me["whatsapp"] == "082 111 2222"
    assert me["max_items"] == 200 and me["name"] == "Paarl Refurb PCs"
    for bad in ({"phone": "12345"}, {"address": "visit www.x.com"}, {"email": "nope"}):
        assert client.post("/api/v1/store/details", json=bad, headers=auth("shopowner")).status_code == 422


def test_an_owner_cannot_touch_another_stores_item(client, owner):
    other = make_store("Other Store")
    link(client, "second", other)
    add(client)
    mine = client.get("/api/v1/store/items", headers=auth("shopowner")).json()["items"][0]
    assert client.delete(f"/api/v1/store/items/{mine['id']}", headers=auth("second")).status_code == 422
    assert (
        client.post(f"/api/v1/store/items/{mine['id']}", json=ITEM, headers=auth("second")).status_code == 422
    )
    assert client.get("/api/v1/store/items", headers=auth("second")).json()["total"] == 0


# --- shoppers -------------------------------------------------------------------------------


def seen(client, who="shopper", **params):
    return client.get("/api/v1/catalogue", params={"region": "paarl", **params}, headers=auth(who)).json()


def test_shoppers_see_the_store_its_items_tags_and_filters(client, owner):
    client.post("/api/v1/store/import", json={"csv": CSV}, headers=auth("shopowner"))
    verify(client, "shopper")
    cat = seen(client, category="Electronics")
    assert {i["name"] for i in cat["items"]} >= {"HP EliteBook 840 G5", "iPhone 11 64GB"}
    assert [x["name"] for x in cat["subs"]][:2] == ["Laptops", "Desktops"]
    laptop = next(i for i in cat["items"] if i["name"].startswith("HP"))
    assert (
        laptop["sub"] == "Laptops"
        and laptop["condition"] == "Refurbished"
        and laptop["store_name"] == "Paarl Refurb PCs"
    )
    assert [i["name"] for i in seen(client, category="Electronics", sub="Desktops")["items"]] == [
        "Dell OptiPlex 7050 SFF"
    ]
    assert [i["name"] for i in seen(client, category="Electronics", condition="New")["items"]] == [
        "Logitech K120 Keyboard"
    ]
    st = client.get(f"/api/v1/stores/{owner}", params={"region": "paarl"}, headers=auth("shopper")).json()
    assert (
        st["name"] == "Paarl Refurb PCs" and st["phone"] == "082 123 4567" and st["whatsapp"] == "27821234567"
    )
    assert st["items"] == 4 and st["address"].startswith("12 Main")
    page = seen(client, store_id=owner)
    assert len(page["items"]) == 4 and all(i["store_id"] == owner for i in page["items"])
    assert (
        client.get("/api/v1/stores/999", params={"region": "paarl"}, headers=auth("shopper")).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/stores/{owner}", params={"region": "wellington"}, headers=auth("shopper")
        ).status_code
        == 404
    )


def test_a_chain_store_has_no_page(client, owner):
    s = get_settings()
    with session_factory(s.database_url)() as db:
        chain = db.scalars(select(Store).where(Store.kind == "chain")).first()
        cid = chain.id
    verify(client, "shopper")
    assert (
        client.get(f"/api/v1/stores/{cid}", params={"region": "paarl"}, headers=auth("shopper")).status_code
        == 404
    )


def test_local_store_prices_carry_contact_details_chains_do_not(client, owner):
    client.post("/api/v1/store/import", json={"csv": CSV}, headers=auth("shopowner"))
    verify(client, "shopper")
    ids = {i["name"]: i["id"] for i in seen(client, category="Electronics")["items"]}
    r = client.post(
        "/api/v1/session/items",
        json={"product_id": ids["HP EliteBook 840 G5"], "region": "paarl"},
        headers=auth("shopper"),
    ).json()
    price = r["items"][-1]["product"]["prices"][0]
    assert price["local"] and price["town"] == "Paarl" and price["whatsapp"] == "27821234567"
    assert price["condition"] == "Refurbished" and price["sub"] == "Laptops" and price["store_id"] == owner
    milk = next(i["id"] for i in seen(client, q="clover")["items"])
    r = client.post(
        "/api/v1/session/items", json={"product_id": milk, "region": "paarl"}, headers=auth("shopper")
    ).json()
    assert all(
        p["phone"] == "" and p["whatsapp"] == "" and p["store_id"] == 0
        for it in r["items"]
        if it["product"]["id"] == milk
        for p in it["product"]["prices"]
    )


# --- paid until -----------------------------------------------------------------------------


def set_paid(store_id, when):
    s = get_settings()
    with session_factory(s.database_url).begin() as db:
        db.get(Store, store_id).paid_until = when


def test_a_lapsed_store_disappears_after_the_grace_days_and_cannot_edit(client, owner):
    client.post("/api/v1/store/import", json={"csv": CSV}, headers=auth("shopowner"))
    verify(client, "shopper")
    set_paid(owner, utcnow() + timedelta(days=5))
    assert client.get("/api/v1/store/me", headers=auth("shopowner")).json()["status"] == "active"
    assert len(seen(client, category="Electronics")["items"]) == 4
    set_paid(owner, utcnow() - timedelta(days=1))  # in the grace days: still shown, owner told
    assert client.get("/api/v1/store/me", headers=auth("shopowner")).json()["status"] == "grace"
    assert len(seen(client, category="Electronics")["items"]) == 4
    assert add(client, name="Another refurbished laptop").status_code == 200
    set_paid(owner, utcnow() - timedelta(days=4))  # past the grace: hidden
    assert seen(client, category="Electronics")["items"] == []
    assert (
        client.get(f"/api/v1/stores/{owner}", params={"region": "paarl"}, headers=auth("shopper")).status_code
        == 404
    )
    me = client.get("/api/v1/store/me", headers=auth("shopowner")).json()
    assert me["status"] == "lapsed"  # the owner can still see everything
    assert client.get("/api/v1/store/items", headers=auth("shopowner")).json()["total"] == 5
    r = add(client, name="Yet another laptop")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "lapsed"
    assert (
        client.post("/api/v1/store/import", json={"csv": CSV}, headers=auth("shopowner")).status_code == 409
    )
    assert (
        client.post(
            "/api/v1/store/import", json={"csv": CSV, "dry_run": True}, headers=auth("shopowner")
        ).status_code
        == 200
    )
    set_paid(owner, None)  # no expiry: visible again
    assert len(seen(client, category="Electronics")["items"]) == 5


def test_confirm_prices_refreshes_the_dates(client, owner):
    add(client)
    s = get_settings()
    with session_factory(s.database_url).begin() as db:
        from pricepulse.db import Price

        for p in db.scalars(select(Price)).all():
            if p.listing.store_id == owner:
                p.scraped_at = utcnow() - timedelta(days=10)
    r = client.post("/api/v1/store/confirm-prices", headers=auth("shopowner")).json()
    assert r["confirmed"] == 1
    item = client.get("/api/v1/store/items", headers=auth("shopowner")).json()["items"][0]
    assert item["updated"] > (utcnow() - timedelta(minutes=5)).isoformat()


# --- admin side -----------------------------------------------------------------------------


def test_admin_links_an_owner_and_sets_paid_until(client):
    verify(client, "boss", ADULT)
    s = get_settings()
    with session_factory(s.database_url).begin() as db:
        db.scalars(select(AppUser)).first().is_admin = True
    sid = make_store()
    client.get("/api/v1/me", headers=auth("shopowner"))
    uid = "dev-shopowner"
    r = client.post(f"/api/v1/admin/localstores/{sid}/owner", json={"uid": uid}, headers=auth("boss"))
    assert r.status_code == 200 and r.json()["owners"] == [uid]
    assert (
        client.post(
            f"/api/v1/admin/localstores/{sid}/owner", json={"uid": "nobody"}, headers=auth("boss")
        ).status_code
        == 422
    )
    st = client.get("/api/v1/admin/localstores", headers=auth("boss")).json()["stores"][0]
    body = {
        k: st[k]
        for k in (
            "name",
            "region",
            "town",
            "address",
            "contact_name",
            "phone",
            "email",
            "notes",
            "active",
            "store_type",
            "max_items",
        )
    }
    r = client.post(
        f"/api/v1/admin/localstores/{sid}", json={**body, "paid_until": "2031-01-31"}, headers=auth("boss")
    )
    assert (
        r.status_code == 200
        and r.json()["paid_until"] == "2031-01-31"
        and r.json()["category"] == "Electronics"
    )
    r = client.post(
        f"/api/v1/admin/localstores/{sid}", json={**body, "paid_until": "31 Jan"}, headers=auth("boss")
    )
    assert r.status_code == 422
    assert client.get("/api/v1/store/me", headers=auth("shopowner")).status_code == 200
    assert (
        client.delete(f"/api/v1/admin/localstores/{sid}/owner/{uid}", headers=auth("boss")).status_code == 200
    )
    assert client.get("/api/v1/store/me", headers=auth("shopowner")).status_code == 404


def test_migration_12_keeps_existing_data(settings):
    from sqlalchemy import text

    from pricepulse.db import make_engine, migrate

    eng = make_engine(settings.database_url)
    migrate(eng)
    with eng.begin() as c:
        c.execute(text("DELETE FROM schema_version WHERE version = 12"))
    migrate(eng)  # runs 12 again on a database that already has the columns: harmless
    assert localstores.DEFAULT_MAX_ITEMS == 1000
