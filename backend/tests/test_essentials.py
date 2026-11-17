"""Essential items: CSV import from the admin area, and the list the scraper machine fetches."""

import pytest
from conftest import make_id
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_accounts_api import auth, seed, verify

import fetch_terms
from pricepulse import essentials
from pricepulse.api.app import create_app
from pricepulse.db import AppUser, session_factory
from run_scrapers import choose_terms

TOKEN = "t" * 40


@pytest.fixture
def app_settings(settings):
    settings.open_prices = False
    settings.ingest_token = TOKEN
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


def test_clean_term_and_category():
    assert essentials.clean_term("  White   Mushrooms 250g ") == "White Mushrooms 250g"
    for bad in ("x", "see www.spam.com", "a" * 81, "<b>bold</b>", "buy at https://x.y"):
        with pytest.raises(ValueError):
            essentials.clean_term(bad)
    assert essentials.clean_category("") == "Groceries"
    assert essentials.clean_category("health and beauty") == "Health & Beauty"
    assert essentials.clean_category("FRESH") == "Fresh"
    with pytest.raises(ValueError):
        essentials.clean_category("Electronics")


def test_only_admins_reach_the_essentials(api):
    for path in ("essentials",):
        assert api.get(f"/api/v1/admin/{path}", headers=auth("alice")).status_code == 404
    r = api.post(
        "/api/v1/admin/essentials/import", json={"rows": [{"term": "milk 2l"}]}, headers=auth("alice")
    )
    assert r.status_code == 404


def test_import_adds_skips_and_reports_bad_rows(api):
    h = auth("boss")
    rows = [
        {"term": "White Mushrooms 250g", "category": "Fresh"},
        {"term": "white mushrooms 250g", "category": "Fresh"},  # same word twice in the file
        {"term": "Koo Baked Beans 410g", "category": ""},
        {"term": "x", "category": "Fresh"},
        {"term": "Panado 24s", "category": "Electronics"},
        {"term": "Castle Lite 6 x 330ml", "category": "liquor"},
    ]
    r = api.post("/api/v1/admin/essentials/import", json={"rows": rows}, headers=h).json()
    assert r["added"] == 3 and r["existing"] == 1 and r["invalid_count"] == 2
    assert {x["why"].split(" ")[0] for x in r["invalid"]} == {"needs", "category"}
    again = api.post("/api/v1/admin/essentials/import", json={"rows": rows[:1]}, headers=h).json()
    assert again["added"] == 0 and again["existing"] == 1  # running the same file twice is safe
    items = api.get("/api/v1/admin/essentials", headers=h).json()["items"]
    assert [(i["term"], i["category"]) for i in items] == [
        ("White Mushrooms 250g", "Fresh"),
        ("Koo Baked Beans 410g", "Groceries"),
        ("Castle Lite 6 x 330ml", "Liquor"),
    ]
    assert {i["source"] for i in items} == {"csv"}
    assert api.post("/api/v1/admin/essentials/import", json={"rows": []}, headers=h).status_code == 422
    gone = api.post("/api/v1/admin/essentials/remove", json={"id": items[0]["id"]}, headers=h)
    assert (
        gone.status_code == 200 and len(api.get("/api/v1/admin/essentials", headers=h).json()["items"]) == 2
    )
    assert api.post("/api/v1/admin/essentials/remove", json={"id": 99999}, headers=h).status_code == 404


def test_approving_a_request_puts_it_on_the_search_list(api):
    h = auth("boss")
    api.post("/api/v1/requests", json={"term": "Oat Milk 1L"}, headers=auth("alice"))
    done = api.post(
        "/api/v1/admin/requests/status", json={"term": "oat milk 1l", "status": "added"}, headers=h
    ).json()
    assert done["search_list"] is True
    items = api.get("/api/v1/admin/essentials", headers=h).json()["items"]
    assert [(i["term"], i["source"]) for i in items] == [("Oat Milk 1L", "request")]
    # declining adds nothing
    api.post("/api/v1/requests", json={"term": "Dragon fruit"}, headers=auth("alice"))
    api.post("/api/v1/admin/requests/status", json={"term": "dragon fruit", "status": "declined"}, headers=h)
    assert len(api.get("/api/v1/admin/essentials", headers=h).json()["items"]) == 1


def test_scraper_machine_fetches_the_list_with_the_upload_token(api):
    api.post(
        "/api/v1/admin/essentials/import",
        json={
            "rows": [
                {"term": "Rooibos tea 80s", "category": "Groceries"},
                {"term": "Panado 24s", "category": "Health & Beauty"},
            ]
        },
        headers=auth("boss"),
    )
    assert api.get("/api/v1/ingest/terms").status_code == 401
    assert (
        api.get("/api/v1/ingest/terms", headers=auth("boss")).status_code == 401
    )  # a sign-in is not the token
    r = api.get("/api/v1/ingest/terms", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    got = {(t["term"], t["category"]) for t in r.json()["terms"]}
    assert got == {("Rooibos tea 80s", "Groceries"), ("Panado 24s", "Health & Beauty")}


def test_fetched_words_join_products_txt_without_doubles(tmp_path):
    products = tmp_path / "products.txt"
    products.write_text("# category: Groceries\nmilk 2l\nbread 700g\n")
    server = tmp_path / "server_terms.txt"
    server.write_text(
        fetch_terms.to_text(
            [
                {"term": "Milk 2L", "category": "Fresh"},  # already in products.txt: searched once
                {"term": "Panado 24s", "category": "Health & Beauty"},
            ]
        )
    )
    both = choose_terms(products, None, None, server)
    assert [t for t, _ in both] == ["milk 2l", "bread 700g", "Panado 24s"]
    assert dict(both)["milk 2l"] == "Groceries"
    assert choose_terms(products, "Health & Beauty", None, server) == [("Panado 24s", "Health & Beauty")]
    assert choose_terms(products, None, None, tmp_path / "missing.txt") == [
        ("milk 2l", "Groceries"),
        ("bread 700g", "Groceries"),
    ]
    assert choose_terms(products, None, ["typed"], server) == [("typed", "Groceries")]  # typed words win


def test_fetch_writes_the_file_and_survives_a_dead_server(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PP_UPLOAD_TOKEN", TOKEN)
    monkeypatch.setattr(
        fetch_terms, "fetch", lambda url, token, timeout=30.0: [{"term": "Eggs 18", "category": "Fresh"}]
    )
    assert fetch_terms.main([]) == 0
    text = (tmp_path / "server_terms.txt").read_text()
    assert "# category: Fresh" in text and "Eggs 18" in text and TOKEN not in text

    def dead(url, token, timeout=30.0):
        raise OSError("no route")

    monkeypatch.setattr(fetch_terms, "fetch", dead)
    assert fetch_terms.main([]) == 1
    assert "Eggs 18" in (tmp_path / "server_terms.txt").read_text()  # last copy kept
    assert TOKEN not in capsys.readouterr().out
