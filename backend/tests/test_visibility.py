"""One clean card per essential item: only each store's best search hit is shown to shoppers."""

import pytest
from conftest import make_id, row
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from test_accounts_api import add, auth, verify

from pricepulse.api.app import create_app
from pricepulse.db import AppUser, Product, make_engine, migrate, session_factory, utcnow
from pricepulse.ingest import ingest_items
from pricepulse.ranking import match_score, rank_hits
from pricepulse.scrapers.base import Item


def test_closest_hit_ranks_first_and_size_counts():
    hits = [
        Item("Checkers", "Mushroom Soup 400g", "400g", "Groceries", 20),
        Item("Checkers", "Sliced Brown Mushrooms Punnet 400g", "400g", "Groceries", 30),
        Item("Checkers", "Brown Mushrooms 250g", "250g", "Groceries", 25),
        Item("Checkers", "Brown Mushrooms 500g", "500g", "Groceries", 45),
    ]
    ranked = rank_hits("brown mushrooms 250g", hits)
    assert ranked[0].name == "Brown Mushrooms 250g"
    assert ranked[-1].name == "Mushroom Soup 400g"
    assert match_score("milk 2l", "Clover Milk 2L", "2L") > match_score("milk 2l", "Clover Milk 1L", "1L")
    assert [h.name for h in rank_hits("milk", hits)] == [
        h.name for h in rank_hits("milk", list(hits))
    ]  # stable


def test_item_rows_carry_the_term_and_rank():
    it = Item("Checkers", "Milk 2L", "2L", "Groceries", 30, term="milk 2l", rank=1)
    assert it.to_dict()["term"] == "milk 2l" and it.to_dict()["rank"] == 1
    plain = Item("Checkers", "Milk 2L", "2L", "Groceries", 30).to_dict()
    assert "rank" not in plain and "term" not in plain


@pytest.fixture
def app_settings(settings):
    settings.open_prices = False
    return settings


def ranked(r, rank, term):
    return {**r, "rank": rank, "term": term}


@pytest.fixture
def api(app_settings):
    rows = [
        ranked(row("Checkers", "Brown Mushrooms 250g", 25.0), 1, "brown mushrooms 250g"),
        ranked(row("Checkers", "Sliced Mushrooms Punnet 400g", 33.0), 2, "brown mushrooms 250g"),
        ranked(row("Checkers", "Mushroom Soup 400g", 18.0), 3, "brown mushrooms 250g"),
        row("Checkers", "Clover Full Cream Milk 2L", 36.0),  # loaded before ranking existed: no rank
        ranked(row("Pick n Pay", "Clover Full Cream Milk 2L", 38.0), 2, "milk 2l"),
    ]
    with session_factory(app_settings.database_url).begin() as s:
        ingest_items(s, rows, "paarl", utcnow(), app_settings)
        s.scalars(select(Product).where(Product.name.like("Sliced%"))).one()
    client = TestClient(create_app(app_settings))
    verify(client, "boss")
    verify(client, "alice", id_number=make_id("900101"))
    with session_factory(app_settings.database_url).begin() as s:
        s.scalars(select(AppUser).where(AppUser.uid == "dev-boss")).one().is_admin = True
    return client


def names(client, who="alice"):
    r = client.get("/api/v1/catalogue", params={"limit": 100}, headers=auth(who)).json()
    return sorted(i["name"] for i in r["items"])


def test_shoppers_see_only_the_best_match_and_unranked_items(api):
    shown = names(api)
    assert "Brown Mushrooms" in " ".join(shown)
    assert not any("Sliced" in n or "Soup" in n for n in shown)  # extra hits are hidden
    assert any("Full Cream Milk" in n for n in shown)  # old data without a rank stays visible
    assert api.get("/api/v1/catalogue", params={"q": "soup"}, headers=auth("alice")).json()["total"] == 0


def test_a_hidden_product_cannot_be_looked_up_but_an_existing_one_stays(api):
    hidden = api.get("/api/v1/admin/items", params={"shown": "no"}, headers=auth("boss")).json()["items"]
    assert {i["name"] for i in hidden} == {"Sliced Mushrooms Punnet 400g", "Mushroom Soup 400g"}
    assert add(api, hidden[0]["product_id"]).status_code == 404
    # an item already on someone's list is not pulled away when it becomes hidden
    milk = next(
        i for i in api.get("/api/v1/catalogue", headers=auth("alice")).json()["items"] if "Milk" in i["name"]
    )
    assert add(api, milk["id"]).status_code == 200
    assert (
        api.post(
            f"/api/v1/admin/products/{milk['id']}/visibility", json={"state": "hide"}, headers=auth("boss")
        ).status_code
        == 200
    )
    assert not any("Milk" in n for n in names(api))
    assert any(
        "Milk" in i["product"]["name"]
        for i in api.get("/api/v1/session", headers=auth("alice")).json()["items"]
    )
    assert add(api, milk["id"]).status_code == 200  # adding it again is the same item


def test_admin_can_show_hide_and_reset_any_product(api):
    h = auth("boss")
    items = api.get("/api/v1/admin/items", headers=h).json()["items"]
    soup = next(i for i in items if "Soup" in i["name"])
    assert (
        soup["shown"] is False
        and soup["visibility"] == "auto"
        and soup["hit_rank"] == 3
        and soup["term"].startswith("brown")
    )
    assert (
        api.post(
            f"/api/v1/admin/products/{soup['product_id']}/visibility", json={"state": "show"}, headers=h
        ).status_code
        == 200
    )
    assert any("Soup" in n for n in names(api))
    api.post(f"/api/v1/admin/products/{soup['product_id']}/visibility", json={"state": "auto"}, headers=h)
    assert not any("Soup" in n for n in names(api))
    assert (
        api.post(
            f"/api/v1/admin/products/{soup['product_id']}/visibility", json={"state": "maybe"}, headers=h
        ).status_code
        == 422
    )
    assert (
        api.post("/api/v1/admin/products/999999/visibility", json={"state": "hide"}, headers=h).status_code
        == 404
    )
    assert (
        api.post(
            f"/api/v1/admin/products/{soup['product_id']}/visibility",
            json={"state": "show"},
            headers=auth("alice"),
        ).status_code
        == 404
    )
    only_shown = api.get("/api/v1/admin/items", params={"shown": "yes"}, headers=h).json()["items"]
    assert all(i["shown"] for i in only_shown)
    ov = api.get("/api/v1/admin/overview", headers=h).json()
    assert ov["products_shown"] < ov["products"]


def test_a_product_found_again_is_ranked_again(app_settings):
    with session_factory(app_settings.database_url).begin() as s:
        a = ranked(row("Checkers", "Eggs 18", 60.0), 2, "eggs 18")
        ingest_items(s, [a], "paarl", utcnow(), app_settings)
        ingest_items(s, [{**a, "rank": 1}], "paarl", utcnow(), app_settings)
        from pricepulse.db import Listing

        assert s.scalar(select(Listing.hit_rank)) == 1


def test_migration_7_adds_the_columns_to_an_old_database(tmp_path):
    url = f"sqlite:///{tmp_path / 'old.db'}"
    engine = make_engine(url)
    migrate(engine)
    with engine.begin() as c:
        c.execute(text("ALTER TABLE listing DROP COLUMN hit_rank"))
        c.execute(text("ALTER TABLE listing DROP COLUMN term"))
        c.execute(text("ALTER TABLE product DROP COLUMN visibility"))
        c.execute(text("DELETE FROM schema_version WHERE version = 7"))
    assert migrate(engine) == [7]
    with engine.begin() as c:
        c.execute(text("SELECT hit_rank, term FROM listing"))
        c.execute(text("SELECT visibility FROM product"))
    assert migrate(engine) == []
