"""New-item searches: what is searched, who may ask, the queue for the scraper machine, and the New tag."""

import json
from datetime import timedelta

import pytest
from conftest import row
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_accounts_api import MINOR, auth, seed, verify

from pricepulse import discovery
from pricepulse.api.app import create_app
from pricepulse.config import get_settings
from pricepulse.db import AppUser, Product, SearchJob, session_factory, utcnow

TOKEN = "t" * 40


@pytest.fixture
def client(settings, monkeypatch):
    settings.open_prices = False
    monkeypatch.setenv("PP_INGEST_TOKEN", TOKEN)
    seed(settings)
    return TestClient(create_app(get_settings()))


def make_paid(client, name="alice"):
    s = get_settings()
    with session_factory(s.database_url).begin() as db:
        db.scalars(select(AppUser)).first().plan_override = "paid"


def worker():
    return {"Authorization": f"Bearer {TOKEN}"}


# --- which words are searched ------------------------------------------------------


@pytest.mark.parametrize(
    ("term", "category"),
    [
        ("Pedigree dog food 8kg", "Pets"),
        ("cat litter", "Pets"),
        ("whiskas kitten pouches", "Pets"),
        ("Savanna Dry cider 6 x 330ml", "Liquor"),
        ("klipdrift brandy 750ml", "Liquor"),
        ("Colgate toothpaste 100ml", "Health & Beauty"),
        ("Panado tablets 24", "Health & Beauty"),
        ("baby spinach 200g", "Fresh"),
        ("rump steak", "Fresh"),
        ("Koo baked beans 410g", "Groceries"),
        ("tomato sauce 700ml", "Groceries"),
        ("aQuelle watermelon flavoured sparkling water 500ml", "Groceries"),
        ("still bottled water 5l", "Groceries"),
        ("sparkling wine 750ml", "Liquor"),
    ],
)
def test_words_get_the_right_category(term, category):
    v = discovery.classify(term)
    assert v.ok and v.category == category, (term, v)


@pytest.mark.parametrize(
    "term", ["laptop 15 inch", "Samsung TV 55", "hunting rifle", "dagga", "vape juice", "sofa", "x", "!!"]
)
def test_other_goods_and_banned_things_are_not_searched(term):
    assert not discovery.classify(term).ok


def test_liquor_is_for_adults_only():
    assert discovery.classify("castle lager 6 x 340ml", adult=True).category == "Liquor"
    v = discovery.classify("castle lager 6 x 340ml", adult=False)
    assert not v.ok and "18" in v.reason


# --- who may search ------------------------------------------------------------------


def test_every_account_can_start_a_search(client):
    verify(client)
    r = client.post("/api/v1/searches", json={"term": "pedigree dog food 8kg"}, headers=auth())
    assert r.status_code == 200
    body = r.json()
    assert (
        body["status"] == "pending" and body["category"] == "Pets" and body["term"] == "pedigree dog food 8kg"
    )
    assert client.get("/api/v1/searches").status_code == 401


def test_a_minor_cannot_search_for_liquor(client):
    verify(client, id_number=MINOR)
    make_paid(client)
    r = client.post("/api/v1/searches", json={"term": "castle lager"}, headers=auth())
    assert r.status_code == 422 and r.json()["detail"]["code"] == "not_covered"


def test_the_same_word_is_searched_once_and_the_daily_limit_holds(client):
    verify(client)
    make_paid(client)
    a = client.post("/api/v1/searches", json={"term": "Pedigree  Dog Food"}, headers=auth()).json()
    b = client.post("/api/v1/searches", json={"term": "pedigree dog food"}, headers=auth()).json()
    assert a["id"] == b["id"]
    for i in range(4):
        assert (
            client.post(
                "/api/v1/searches", json={"term": f"rooibos tea blend {i}"}, headers=auth()
            ).status_code
            == 200
        )
    r = client.post("/api/v1/searches", json={"term": "one more tin of beans"}, headers=auth())
    assert r.status_code == 429 and r.json()["detail"]["code"] == "daily_limit"


def test_a_tester_account_has_no_daily_search_limit(client):
    verify(client)
    s = get_settings()
    with session_factory(s.database_url).begin() as db:
        db.scalars(select(AppUser)).first().plan_override = "tester"
    for i in range(8):
        r = client.post("/api/v1/searches", json={"term": f"rooibos tea blend {i}"}, headers=auth())
        assert r.status_code == 200, r.json()


# --- the scraper machine's side -------------------------------------------------------


def test_the_scraper_machine_claims_searches_and_reports_back(client):
    verify(client)
    make_paid(client)
    client.post("/api/v1/searches", json={"term": "pedigree dog food 8kg"}, headers=auth())
    assert client.get("/api/v1/ingest/searches").status_code == 401  # needs the upload token
    got = client.get("/api/v1/ingest/searches", headers=worker()).json()["searches"]
    assert [g["term"] for g in got] == ["pedigree dog food 8kg"] and got[0]["category"] == "Pets"
    assert client.get("/api/v1/ingest/searches", headers=worker()).json()["searches"] == []  # now running
    mine = client.get("/api/v1/searches", headers=auth()).json()["searches"]
    assert mine[0]["status"] == "running"
    r = client.post(f"/api/v1/ingest/searches/{got[0]['id']}", json={"found": 3}, headers=worker())
    assert r.status_code == 200 and r.json()["status"] == "done"
    mine = client.get("/api/v1/searches", headers=auth()).json()["searches"]
    assert mine[0]["status"] == "done" and mine[0]["found"] == 3
    assert client.post("/api/v1/ingest/searches/999", json={"found": 0}, headers=worker()).status_code == 404


def test_a_search_that_never_reports_back_goes_back_in_the_queue(client):
    verify(client)
    make_paid(client)
    client.post("/api/v1/searches", json={"term": "pedigree dog food 8kg"}, headers=auth())
    client.get("/api/v1/ingest/searches", headers=worker())
    s = get_settings()
    with session_factory(s.database_url).begin() as db:
        job = db.scalars(select(SearchJob)).one()
        job.claimed_at = utcnow() - timedelta(hours=1)
    assert len(client.get("/api/v1/ingest/searches", headers=worker()).json()["searches"]) == 1


def test_a_failed_search_can_be_started_again(client):
    verify(client)
    make_paid(client)
    j = client.post("/api/v1/searches", json={"term": "pedigree dog food 8kg"}, headers=auth()).json()
    client.get("/api/v1/ingest/searches", headers=worker())
    client.post(
        f"/api/v1/ingest/searches/{j['id']}",
        json={"found": 0, "error": "The stores could not be searched."},
        headers=worker(),
    )
    again = client.post("/api/v1/searches", json={"term": "pedigree dog food 8kg"}, headers=auth()).json()
    assert again["id"] != j["id"] and again["status"] == "pending"


# --- found items carry the New tag -----------------------------------------------------


def test_items_found_by_a_search_are_marked_new_and_ordinary_ones_are_not(client):
    verify(client)
    s = get_settings()
    from pricepulse.ingest import ingest_items

    rows = [
        {**row("Checkers", "Pedigree Adult Dog Food 8kg", 199.99, category="Pets"), "new": True},
        {**row("Pick n Pay", "Pedigree Adult Dog Food 8kg", 205.0, category="Pets"), "new": True},
    ]
    with session_factory(s.database_url).begin() as db:
        ingest_items(db, rows, "paarl", utcnow(), s)
        pedigree = db.scalars(select(Product).where(Product.name.like("Pedigree%"))).one()
        assert pedigree.via_search
    cat = client.get("/api/v1/catalogue", params={"q": "pedigree"}, headers=auth()).json()["items"]
    assert [i["new"] for i in cat] == [True]
    milk = client.get("/api/v1/catalogue", params={"q": "clover"}, headers=auth()).json()["items"]
    assert milk and not any(i["new"] for i in milk)
    # the tag fades after two weeks
    with session_factory(s.database_url).begin() as db:
        db.scalars(select(Product).where(Product.name.like("Pedigree%"))).one().created_at = (
            utcnow() - timedelta(days=20)
        )
    cat = client.get("/api/v1/catalogue", params={"q": "pedigree"}, headers=auth()).json()["items"]
    assert [i["new"] for i in cat] == [False]


def test_deleting_my_data_removes_my_searches(client):
    verify(client)
    make_paid(client)
    client.post("/api/v1/searches", json={"term": "pedigree dog food 8kg"}, headers=auth())
    assert client.request("DELETE", "/api/v1/me", headers=auth()).status_code == 200
    s = get_settings()
    with session_factory(s.database_url)() as db:
        assert db.scalars(select(SearchJob)).all() == []


# --- the worker's small pieces ---------------------------------------------------------


def test_worker_helpers():
    import search_worker as w

    assert w.products_text("  dog   food ", "Pets") == "# category: Pets\ndog food\n"
    data = {"items": [{"name": "a"}, {"name": "b"}]}
    assert w.mark_new(data) == 2 and all(r["new"] for r in data["items"])
    assert w.mark_new({}) == 0
    json.dumps(data)  # still plain JSON
