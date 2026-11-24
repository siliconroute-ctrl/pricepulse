"""Photo search: the phone sends a barcode or label words; we check them and keep to groceries."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_accounts_api import MINOR, auth, seed, verify

from pricepulse import abuse
from pricepulse.api.app import create_app
from pricepulse.config import get_settings
from pricepulse.db import AppUser, Listing, PhotoUse, Product, session_factory
from pricepulse.photosearch import DAILY_LIMIT, code_variants, words_from


@pytest.fixture
def client(settings):
    settings.open_prices = False
    seed(settings)
    s = get_settings()
    with session_factory(s.database_url).begin() as db:
        for lst in db.scalars(select(Listing)):
            if lst.name.startswith("Clover"):
                lst.barcode = "6001234567892"
            elif lst.name.startswith("Castle Lite"):
                lst.barcode = "6009876543210"
    abuse.reset()
    return TestClient(create_app(get_settings()))


def ask(client, who="alice", **params):
    return client.get("/api/v1/photo-search", params={"region": "paarl", **params}, headers=auth(who))


def test_helpers():
    assert "6001234567892" in code_variants("6001234567892")
    assert "0012345678905" in code_variants("012345678905") and "12345678905" in code_variants("012345678905")
    assert code_variants("12") == []
    assert words_from("THE Clover Full Cream Milk 2L  best before 2027 net 2000") == [
        "clover", "full", "cream", "milk",
    ]  # fmt: skip


def test_a_barcode_finds_the_grocery_item(client):
    verify(client)
    r = ask(client, code="6001234567892")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "found"
    assert body["items"][0]["name"].startswith("Clover") and body["items"][0]["category"] == "Fresh"
    assert body["left_today"] == DAILY_LIMIT - 1
    # a UPC-A style scan (leading zero dropped or added) still matches
    assert ask(client, code="06001234567892").json()["status"] == "found"


def test_label_words_find_the_item_and_loose_words_do_not(client):
    verify(client)
    body = ask(client, text="Clover FULL CREAM milk 2L best before").json()
    assert body["status"] == "found" and body["items"][0]["name"].startswith("Clover")
    assert ask(client, text="purple elephant dancing").json()["status"] == "not_found"
    assert ask(client, text="milk").json()["status"] == "unreadable"  # one word is not enough


def test_things_that_are_not_groceries_are_refused(client):
    verify(client)
    s = get_settings()
    with session_factory(s.database_url).begin() as db:
        milk = db.scalars(select(Product).where(Product.name.like("Albany%"))).first()
        milk.category = "Electronics"
        for lst in db.scalars(select(Listing).where(Listing.product_id == milk.id)):
            lst.barcode = "6005550001111"
    body = ask(client, code="6005550001111").json()
    assert body["status"] == "refused" and "not a grocery item" in body["message"] and body["items"] == []


def test_liquor_is_for_adults_only(client):
    verify(client, "adult")
    verify(client, "kid", id_number=MINOR)
    assert ask(client, "adult", code="6009876543210").json()["status"] == "found"
    kid = ask(client, "kid", code="6009876543210").json()
    assert kid["status"] == "refused" and "18" in kid["message"]


def test_twenty_photo_searches_a_day_and_nothing_to_search_is_refused(client):
    verify(client)
    for _ in range(DAILY_LIMIT):
        assert ask(client, code="6001234567892").status_code == 200
    r = ask(client, code="6001234567892")
    assert r.status_code == 429 and r.json()["detail"]["code"] == "photo_limit"
    s = get_settings()
    with session_factory(s.database_url)() as db:
        assert db.scalars(select(PhotoUse)).one().count == DAILY_LIMIT
    verify(client, "tester")
    with session_factory(s.database_url).begin() as db:
        db.scalars(select(AppUser).where(AppUser.uid == "dev-tester")).one().plan_override = "tester"
    assert all(ask(client, "tester", code="6001234567892").status_code == 200 for _ in range(DAILY_LIMIT + 2))
    assert ask(client, "tester").status_code == 422
    assert client.get("/api/v1/photo-search").status_code == 401
