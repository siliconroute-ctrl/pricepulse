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
    assert names == [
        "Clover Full Cream Milk",
        "Boerewors",
        "Brown Bread",
        "Sherry",
    ]  # repeated SKU counted once
    assert chk.new_categories == ["Electronics"]  # a store's own category is kept, and flagged in the preview
    assert chk.duplicates == 1
    why = {i["name"]: i["why"] for i in chk.invalid}
    assert "price must be" in why["Free Cheese"] and "at least 2" in why["X"] and "Sherry" not in why
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
    assert (rep.added, rep.updated, rep.removed) == (4, 0, 0) and len(rep.invalid) == 2
    assert st.last_submission_at is not None
    lst = db.scalars(select(Listing).where(Listing.store_id == st.id)).all()
    assert {x.sku for x in lst} == {"MILK2", "BW1", "BR7", "SH1"} and {x.source for x in lst} == {"csv"}
    assert all(x.active and x.hit_rank == 1 for x in lst)  # local items are always shown to shoppers
    from pricepulse.queries import latest_prices

    assert latest_prices(db, "paarl") and not latest_prices(db, "wellington")  # only in its own town

    # next week: milk price changes, bread is gone, a new item arrives
    week2 = "item,price,sku\nClover Full Cream Milk,36.00,MILK2\nOats 1kg,29.00,OAT1\n"
    rows2, _ = localstores.parse_csv(week2)
    rep2 = localstores.import_for_store(db, st, rows2, settings, now=utcnow() + timedelta(days=7))
    assert (rep2.added, rep2.updated, rep2.removed) == (1, 1, 3)
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
    assert preview["added"] == 4 and preview["dry_run"] and preview["invalid_count"] == 2
    assert (
        api.get("/api/v1/admin/localstores", headers=h).json()["stores"][0]["items"] == 0
    )  # preview kept nothing
    done = api.post(
        f"/api/v1/admin/localstores/{sid}/import", json={"csv": CSV, "filename": "w1.csv"}, headers=h
    ).json()
    assert done["added"] == 4 and not done["dry_run"]
    row_ = api.get("/api/v1/admin/localstores", headers=h).json()["stores"][0]
    assert row_["items"] == 4 and row_["submissions"] == 1 and row_["last_submission"]
    subs = api.get(f"/api/v1/admin/localstores/{sid}/submissions", headers=h).json()["submissions"]
    assert subs[0]["filename"] == "w1.csv" and subs[0]["added"] == 4 and subs[0]["invalid"] == 2

    # shoppers in Paarl can find and look up the store's items; the price carries the local flag
    cat = api.get("/api/v1/catalogue", params={"q": "boerewors"}, headers=auth("alice")).json()
    assert cat["total"] == 1 and cat["items"][0]["image"] == ""  # this file has no pictures
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


# A wholesaler's tab-separated price list: a "Product" code column comes first, the item name is in
# "Description", the price includes VAT, and some items are out of stock (made-up rows, same shape).
WHOLESALER = (
    "Product\tCategory\tManufacturer\tSubCategory\tDescription\tPriceInclVAT\tStockLevel\tDate\tImageUrl\tDateAdded\n"
    'AB.123.001\tDisplays and Projectors\tACME\tMonitors\tACME MONITOR V20 19.5" LED, HDMI, 3 YEAR CARRY IN\t1149\tCurrently In Stock\t11/19/2026\thttps://img.example/1.jpg\t02/10/2025\n'
    "AB.123.002\tDesktops\tACME\tDesktop PC\t"
    + "ACME VN2595G "
    + "SPEC " * 60
    + "\t17824\tCurrently In Stock\t11/19/2026\thttps://img.example/2.jpg\t29/10/2025\n"
    "AB.123.003\tDesktops\tACME\tDesktop PC\tACME VN1502G TURNKEY\t13799\tCurrently No Stock\t11/19/2026\thttps://img.example/3.jpg\t17/02/2026\n"
)


def test_a_wholesaler_file_maps_by_the_best_header_and_leaves_out_what_is_not_in_stock():
    rows, problems = localstores.parse_csv(WHOLESALER)
    assert not problems and len(rows) == 3
    r0 = rows[0]
    assert (
        r0["name"].startswith("ACME MONITOR") and r0["sku"] == "AB.123.001"
    )  # Description, not the code column
    assert (
        r0["brand"] == "ACME"
        and r0["category"] == "Displays and Projectors"
        and r0["department"] == "Monitors"
    )
    assert r0["price"] == "1149" and r0["stock"] == "Currently In Stock"
    chk = localstores.check_rows(rows)
    assert (
        [r["sku"] for r in chk.ok] == ["AB.123.001", "AB.123.002"]
        and chk.out_of_stock == 1
        and not chk.invalid
    )
    assert len(chk.ok[1]["name"]) <= 200 and chk.ok[1]["name"].endswith(
        "..."
    )  # a long description is shortened
    assert chk.new_categories == ["Displays and Projectors", "Desktops"]
    assert chk.ok[0]["brand"] == "ACME" and chk.ok[0]["price"] == 1149.0


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Currently In Stock", True),
        ("", True),
        ("In Stock", True),
        ("12", True),
        ("Currently No Stock", False),
        ("Out of stock", False),
        ("0", False),
        ("Discontinued", False),
    ],
)
def test_stock_words(value, expected):
    assert localstores.in_stock(value) is expected


def test_the_separator_is_the_one_on_every_line():
    # commas inside the descriptions must not beat the tabs that really separate the columns
    text = "item\tprice\nBolts, nuts, washers\t12\nSpanner, 10mm, steel\t30\n"
    rows, _ = localstores.parse_csv(text)
    assert [r["name"] for r in rows] == ["Bolts, nuts, washers", "Spanner, 10mm, steel"]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://img.example.co.za/a/1.jpg", "https://img.example.co.za/a/1.jpg"),
        ("  https://img.example.com/x.png ", "https://img.example.com/x.png"),
        ("http://img.example.com/x.png", ""),  # not https: mixed content, refused
        ("javascript:alert(1)", ""),
        ("https://", ""),
        ("https://img.example.com/a b.jpg", ""),  # spaces
        ("https://img.example.com/" + "a" * 400, ""),  # too long
        ("/api/v1/img/3/MILK2.jpg", "/api/v1/img/3/MILK2.jpg"),  # our own stored picture
        ("", ""),
    ],
)
def test_picture_links(value, expected):
    assert localstores.clean_image_url(value) == expected


def test_a_picture_column_is_kept_with_the_item_and_a_bad_link_does_not_stop_the_item(db, settings):
    from pricepulse.queries import latest_prices

    st = make_store(db)
    text = (
        "Description,PriceInclVAT,ImageUrl\n"
        "Bolts 10mm,12,https://img.example.com/b.jpg\n"
        "Nuts 10mm,9,http://img.example.com/n.jpg\n"
        "Washers 10mm,5,\n"
    )
    rows, problems = localstores.parse_csv(text)
    assert not problems and rows[0]["image"] == "https://img.example.com/b.jpg"
    rep = localstores.import_for_store(db, st, rows, settings, dry_run=True)
    assert (rep.pictures, rep.bad_pictures) == (1, 1) and rep.as_dict()["bad_pictures"] == 1
    rep = localstores.import_for_store(db, st, rows, settings)
    assert rep.added == 3
    imgs = {x.name: x.image_url for x in db.scalars(select(Listing).where(Listing.store_id == st.id))}
    assert imgs == {"Bolts 10mm": "https://img.example.com/b.jpg", "Nuts 10mm": "", "Washers 10mm": ""}
    prices = latest_prices(db, "paarl")
    bolts = next(p for p in prices.values() if p.product.name.startswith("Bolts"))
    assert bolts.prices[0].image == "https://img.example.com/b.jpg"
    # next week's file without a picture keeps the picture already stored
    again, _ = localstores.parse_csv("item,price\nBolts 10mm,13\n")
    localstores.import_for_store(db, st, again, settings, replace=False)
    assert db.scalar(select(Listing.image_url).where(Listing.name == "Bolts 10mm")) == (
        "https://img.example.com/b.jpg"
    )


def test_pictures_reach_the_catalogue_and_the_product_card(api, app_settings):
    h = auth("boss")
    sid = api.post("/api/v1/admin/localstores", json=STORE, headers=h).json()["id"]
    csv_ = "item,price,size,image\nBoerewors,89.90,1kg,https://img.example.com/w.jpg\n"
    pre = api.post(f"/api/v1/admin/localstores/{sid}/import", json={"csv": csv_, "dry_run": True}, headers=h)
    assert pre.json()["pictures"] == 1 and pre.json()["bad_pictures"] == 0
    assert (
        api.post(f"/api/v1/admin/localstores/{sid}/import", json={"csv": csv_}, headers=h).status_code == 200
    )
    cat = api.get("/api/v1/catalogue", params={"q": "boerewors"}, headers=auth("alice")).json()
    assert cat["items"][0]["image"] == "https://img.example.com/w.jpg"
    assert add(api, cat["items"][0]["id"], "alice").status_code == 200
    prod = api.get("/api/v1/session", params={"region": "paarl"}, headers=auth("alice")).json()["items"][0][
        "product"
    ]
    assert prod["image"] == "https://img.example.com/w.jpg"
    assert prod["prices"][0]["image"] == "https://img.example.com/w.jpg"


def test_a_store_cannot_list_more_than_its_limit(db, settings):
    st = make_store(db)
    st.max_items = 10
    db.flush()
    text = "item,price\n" + "".join(f"Item number {i},{10 + i}\n" for i in range(11))
    rows, _ = localstores.parse_csv(text)
    with pytest.raises(localstores.ImportTooBig) as exc:
        localstores.import_for_store(db, st, rows, settings)
    assert "up to 10 items" in str(exc.value) and "11" in str(exc.value)
    assert db.scalar(select(Listing.id).where(Listing.store_id == st.id)) is None  # nothing was loaded
    ok, _ = localstores.parse_csv("item,price\n" + "".join(f"Item number {i},{10 + i}\n" for i in range(10)))
    assert localstores.import_for_store(db, st, ok, settings).added == 10


def test_prices_are_what_the_buyer_pays_so_a_file_with_only_ex_vat_prices_is_refused():
    rows, problems = localstores.parse_csv("item,Price Excl VAT\nPens,10\n")
    assert rows == [] and "VAT" in problems[0] and "Selling Price" in problems[0]
    # an explicit selling price is used as it is: nothing is ever added to it
    rows2, problems2 = localstores.parse_csv("item,selling price,price excl vat\nPens,11.50,10\n")
    assert not problems2 and rows2[0]["price"] == "11.50"
    chk = localstores.check_rows(rows2)
    assert chk.ok[0]["price"] == 11.5


def test_accounting_export_headers_are_understood():
    rows, problems = localstores.parse_csv(
        "Item Code,Item Name,Selling Price,Stock On Hand,Item Group\nA1,Pens blue box,45.00,12,Stationery\n"
    )
    assert not problems
    r = rows[0]
    assert (r["sku"], r["name"], r["price"], r["stock"], r["category"]) == (
        "A1",
        "Pens blue box",
        "45.00",
        "12",
        "Stationery",
    )


def test_store_settings_through_the_admin_api(api, app_settings):
    h = auth("boss")
    bad = api.post("/api/v1/admin/localstores", json={**STORE, "store_type": "Spaceship"}, headers=h)
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "bad_type"
    low = api.post("/api/v1/admin/localstores", json={**STORE, "max_items": 5}, headers=h)
    assert low.status_code == 422
    made = api.post(
        "/api/v1/admin/localstores",
        json={**STORE, "store_type": "Butchery", "max_items": 20},
        headers=h,
    ).json()
    assert (made["store_type"], made["max_items"]) == ("Butchery", 20)
    big = "item,price\n" + "".join(f"Biltong cut {i},{20 + i}\n" for i in range(21))
    over = api.post(f"/api/v1/admin/localstores/{made['id']}/import", json={"csv": big}, headers=h)
    assert over.status_code == 422 and over.json()["detail"]["code"] == "too_many_items"
    prev = api.post(
        f"/api/v1/admin/localstores/{made['id']}/import",
        json={"csv": "item,price\nBoerewors,100\n", "dry_run": True},
        headers=h,
    ).json()
    assert prev["max_items"] == 20


def test_catalogue_can_show_only_specials_or_only_local_store_items(api, app_settings):
    h = auth("boss")
    sid = api.post("/api/v1/admin/localstores", json=STORE, headers=h).json()["id"]
    csv_ = "item,price,promo price,size\nBoerewors,89.90,79.90,1kg\nBiltong,150,,500g\n"
    assert (
        api.post(f"/api/v1/admin/localstores/{sid}/import", json={"csv": csv_}, headers=h).status_code == 200
    )
    names = lambda r: sorted(i["name"] for i in r.json()["items"])  # noqa: E731
    sp = api.get("/api/v1/catalogue", params={"special": "true", "q": "o"}, headers=auth("alice"))
    assert names(sp) == ["Boerewors 1kg"] or names(sp) == ["Boerewors"]
    item = sp.json()["items"][0]
    assert item["special"] is True and item["local"] is True
    loc = api.get("/api/v1/catalogue", params={"local": "true", "q": "biltong"}, headers=auth("alice"))
    assert loc.json()["total"] == 1 and loc.json()["items"][0]["special"] is False
    allr = api.get("/api/v1/catalogue", params={"q": "biltong"}, headers=auth("alice"))
    assert allr.json()["items"][0]["local"] is True
