"""The private price-upload address: token, limits, and what it loads."""

import json
from datetime import timedelta

import pytest
from conftest import row
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from pricepulse.api.app import create_app
from pricepulse.config import get_settings
from pricepulse.db import Price, ScrapeRun, session_factory, utcnow

TOKEN = "t" * 40
URL = "/api/v1/ingest/prices"


@pytest.fixture
def api(settings, monkeypatch):
    monkeypatch.setenv("PP_INGEST_TOKEN", TOKEN)
    s = get_settings()
    return TestClient(create_app(s)), s


def auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


def payload(when=None, region="paarl", **over):
    when = when or (utcnow() + timedelta(hours=2)) - timedelta(hours=1)  # a South African clock time
    data = {
        "region": region,
        "scraped_at": when.strftime("%Y-%m-%d %H:%M"),
        "items": [
            row("Checkers", "Clover Full Cream Milk 2L", 36.99, category="Fresh", url="https://c.example/m"),
            row("Woolworths", "CLOVER FULL CREAM MILK 2LTR", 41.99, category="Fresh"),
        ],
    }
    data.update(over)
    return json.dumps(data)


def test_no_token_set_means_the_address_does_not_exist(settings):
    client = TestClient(create_app(get_settings()))
    assert client.post(URL, content=payload(), headers=auth()).status_code == 404


def test_a_weak_token_stops_the_app_starting(settings, monkeypatch):
    monkeypatch.setenv("PP_INGEST_TOKEN", "short")
    with pytest.raises(RuntimeError, match="at least 32 characters"):
        create_app(get_settings())


def test_wrong_or_missing_token_is_refused_and_nothing_is_loaded(api):
    client, s = api
    assert client.post(URL, content=payload()).status_code == 401
    assert client.post(URL, content=payload(), headers=auth("x" * 40)).status_code == 401
    with session_factory(s.database_url)() as db:
        assert db.scalar(select(func.count()).select_from(Price)) == 0


def test_repeated_wrong_tokens_are_blocked_even_for_the_right_one(api):
    client, _ = api
    for _ in range(5):
        assert client.post(URL, content=payload(), headers=auth("x" * 40)).status_code == 401
    assert client.post(URL, content=payload(), headers=auth()).status_code == 429


def test_a_good_upload_loads_prices_and_records_a_run_per_store(api):
    client, s = api
    r = client.post(URL, content=payload(), headers=auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["region"] == "paarl" and body["rows"] == 2 and body["saved"] == 2 and body["rejected"] == 0
    with session_factory(s.database_url)() as db:
        assert db.scalar(select(func.count()).select_from(Price)) == 2
        runs = {x.store: x.status for x in db.scalars(select(ScrapeRun)).all()}
    assert runs == {"Checkers": "ok", "Woolworths": "ok"}
    health = client.get("/api/v1/health").json()
    assert health["status"] == "ok"


def test_the_token_can_only_load_prices(api):
    client, _ = api
    assert client.get("/api/v1/me", headers=auth()).status_code == 401  # not a sign-in token


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        json.dumps({"region": "paarl", "scraped_at": "2026-11-14 10:00"}),  # no items
        json.dumps({"region": "atlantis", "scraped_at": "2026-11-14 10:00", "items": [{"a": 1}]}),
        json.dumps({"region": "paarl", "items": [{"store": "X", "name": "y", "price": 1}]}),  # no date
        json.dumps(
            {"region": "paarl", "scraped_at": "2026-11-14 10:00", "sample": True, "items": [{"a": 1}]}
        ),
        json.dumps({"region": "paarl", "scraped_at": "2026-11-14 10:00", "items": ["text"]}),
    ],
)
def test_bad_files_are_refused(api, bad):
    client, _ = api
    assert client.post(URL, content=bad, headers=auth()).status_code == 422


def test_old_and_future_files_are_refused(api):
    client, _ = api
    old = utcnow() - timedelta(hours=100)
    future = utcnow() + timedelta(days=3)
    assert client.post(URL, content=payload(when=old), headers=auth()).status_code == 422
    assert client.post(URL, content=payload(when=future), headers=auth()).status_code == 422


def test_files_over_the_size_limit_are_refused(settings, monkeypatch):
    monkeypatch.setenv("PP_INGEST_TOKEN", TOKEN)
    monkeypatch.setenv("PP_INGEST_MAX_BYTES", "200")
    client = TestClient(create_app(get_settings()))
    assert client.post(URL, content=payload(), headers=auth()).status_code == 413


def test_too_many_rows_are_refused(settings, monkeypatch):
    monkeypatch.setenv("PP_INGEST_TOKEN", TOKEN)
    monkeypatch.setenv("PP_INGEST_MAX_ITEMS", "1")
    client = TestClient(create_app(get_settings()))
    assert client.post(URL, content=payload(), headers=auth()).status_code == 422
