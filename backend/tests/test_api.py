from datetime import timedelta

import pytest
from conftest import row
from fastapi.testclient import TestClient

from pricepulse.api.app import create_app
from pricepulse.db import ScrapeRun, session_factory, utcnow
from pricepulse.ingest import ingest_items


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


def seed(settings, region="paarl", when=None, extra=()):
    when = when or utcnow()
    Session = session_factory(settings.database_url)
    rows = [
        row("Checkers", "Clover Full Cream Milk 2L", 36.99, category="Fresh", url="https://c.example/milk"),
        row("Pick n Pay", "Clover Fresh Full Cream Milk", 38.49, size="2L", category="Fresh"),
        row("Woolworths", "CLOVER FULL CREAM MILK 2L", 41.99, category="Fresh", promo=35.0),
        row("Checkers", "Albany Superior White Bread 700g", 19.99),
        row("Pick n Pay", "Albany Superior White Bread 700g", 20.99),
        *extra,
    ]
    with Session.begin() as s:
        ingest_items(s, rows, region, when, settings)
        for store in ("Checkers", "Pick n Pay", "Woolworths"):
            s.add(
                ScrapeRun(
                    store=store, region=region, started_at=when, finished_at=when, status="ok", item_count=2
                )
            )


# --- empty database --------------------------------------------------------------


def test_empty_database(client):
    h = client.get("/api/v1/health").json()
    assert h["status"] == "empty" and len(h["stores"]) == 6
    assert all(s["status"] == "never" and s["verified"] is False for s in h["stores"])
    r = client.get("/api/v1/products", params={"region": "paarl"})
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == [] and body["total"] == 0 and body["scraped_at"] is None
    assert body["note"] == "No prices for this town yet."
    t = client.get("/api/v1/towns/compare", params={"region": "paarl"}).json()
    assert t["note"] == "No products to compare yet."
    b = client.post("/api/v1/basket/optimise", json={"region": "paarl", "items": [{"product_id": 1}]}).json()
    assert b["cheapest_single_store"] is None and b["not_found_product_ids"] == [1]
    assert client.get("/api/v1/prices", params={"region": "paarl"}).status_code == 404
    assert client.get("/api/v1/health", params={"strict": True}).status_code == 503


# --- region validation -----------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/v1/products", "/api/v1/towns/compare", "/api/v1/products/1"])
def test_unknown_region_404(client, path):
    r = client.get(path, params={"region": "atlantis"})
    assert r.status_code == 404 and "atlantis" in r.json()["detail"]


@pytest.mark.parametrize("bad", ["Paarl", "paarl!", "../etc", "x" * 41, "a b"])
def test_invalid_region_422(client, bad):
    assert client.get("/api/v1/products", params={"region": bad}).status_code == 422
    assert client.get("/api/v1/prices", params={"region": bad}).status_code == 422


def test_basket_validation(client):
    assert client.post("/api/v1/basket/optimise", json={"region": "paarl", "items": []}).status_code == 422
    assert (
        client.post(
            "/api/v1/basket/optimise", json={"region": "paarl", "items": [{"product_id": 1, "quantity": 0}]}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/basket/optimise", json={"region": "BAD", "items": [{"product_id": 1}]}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/basket/optimise", json={"region": "atlantis", "items": [{"product_id": 1}]}
        ).status_code
        == 404
    )


# --- with data ---------------------------------------------------------------------


def test_products_grouped_with_best_price(client, settings):
    seed(settings)
    body = client.get("/api/v1/products", params={"region": "paarl", "q": "milk"}).json()
    assert body["total"] == 1 and body["sample"] is False and body["scraped_at"].endswith("Z")
    p = body["items"][0]
    assert p["name"] == "Clover Full Cream Milk" and p["size"] == "2L" and p["brand"] == "Clover"
    assert [x["store"] for x in p["prices"]] == ["Woolworths", "Checkers", "Pick n Pay"]
    assert p["best_store"] == "Woolworths" and p["best_price"] == 35.0  # promo wins
    assert p["unit_price"] == {"value": 1.75, "unit": "per 100ml"}
    assert all(x["scraped_at"] and x["stale"] is False for x in p["prices"])


def test_pagination_and_category(client, settings):
    seed(settings)
    r = client.get("/api/v1/products", params={"region": "paarl", "limit": 1, "offset": 1}).json()
    assert r["total"] == 2 and len(r["items"]) == 1
    r = client.get("/api/v1/products", params={"region": "paarl", "category": "Fresh"}).json()
    assert r["total"] == 1
    assert client.get("/api/v1/products", params={"limit": 101}).status_code == 422


def test_stale_flag(client, settings):
    seed(settings, when=utcnow() - timedelta(hours=72))
    p = client.get("/api/v1/products").json()["items"][0]
    assert all(x["stale"] for x in p["prices"])
    assert client.get("/api/v1/health").json()["status"] == "stale"


def test_product_detail_history(client, settings):
    seed(settings, when=utcnow() - timedelta(days=2))
    seed(settings)
    items = client.get("/api/v1/products", params={"q": "milk"}).json()["items"]
    d = client.get(f"/api/v1/products/{items[0]['id']}", params={"region": "paarl"}).json()
    assert d["region"] == "paarl" and len(d["prices"]) == 3
    checkers = next(h for h in d["history"] if h["store"] == "Checkers")
    assert len(checkers["points"]) == 2
    assert client.get("/api/v1/products/9999").status_code == 404


def test_towns_compare_uses_real_regions(client, settings):
    seed(settings, "paarl")
    r = client.get("/api/v1/towns/compare", params={"region": "paarl", "radius_km": 30}).json()
    assert r["note"].startswith("Only one town has prices")
    paarl = next(t for t in r["towns"] if t["region"] == "paarl")
    assert paarl["total"] == 54.99 and paarl["items_missing"] == 0
    assert all(t["distance_km"] <= 30 for t in r["towns"])
    assert not any(t["region"] == "hermanus" for t in r["towns"])
    seed(settings, "wellington", extra=[row("Checkers", "Albany Superior White Bread 700g", 18.0)])
    r = client.get("/api/v1/towns/compare", params={"region": "paarl"}).json()
    names = [t["region"] for t in r["towns"] if t["total"] is not None]
    assert names[0] == "wellington" and r["note"] == ""
    assert client.get("/api/v1/towns/compare", params={"product_ids": "1,x"}).status_code == 422


def test_basket_optimise(client, settings):
    seed(settings)
    ids = {p["name"]: p["id"] for p in client.get("/api/v1/products").json()["items"]}
    body = {
        "region": "paarl",
        "items": [
            {"product_id": ids["Clover Full Cream Milk"], "quantity": 2},
            {"product_id": ids["Albany Superior White Bread"], "quantity": 1},
        ],
    }
    r = client.post("/api/v1/basket/optimise", json=body).json()
    assert r["cheapest_single_store"]["store"] == "Checkers"
    assert r["cheapest_single_store"]["total"] == round(36.99 * 2 + 19.99, 2)
    assert r["split_total"] == round(35.0 * 2 + 19.99, 2)
    assert r["split_saving"] == round(r["cheapest_single_store"]["total"] - r["split_total"], 2)
    woolies = next(s for s in r["stores"] if s["store"] == "Woolworths")
    assert woolies["full_coverage"] is False


def test_regions_endpoint(client, settings):
    seed(settings)
    regions = {r["slug"]: r for r in client.get("/api/v1/regions").json()}
    assert set(regions) >= {
        "paarl",
        "stellenbosch",
        "cape-town",
        "worcester",
        "wellington",
        "franschhoek",
        "hermanus",
    }
    assert regions["paarl"]["has_data"] and not regions["hermanus"]["has_data"]
    assert -35 < regions["paarl"]["lat"] < -33


def test_health_ok(client, settings):
    seed(settings)
    h = client.get("/api/v1/health", params={"strict": True})
    assert h.status_code == 200 and h.json()["status"] == "ok"


# --- file bridge + middleware ---------------------------------------------------------


def test_legacy_prices_file(client, settings):
    (settings.data_dir / "latest_paarl.json").write_text('{"region":"paarl","scraped_at":"x","items":[]}')
    assert client.get("/api/v1/prices", params={"region": "paarl"}).json()["region"] == "paarl"
    assert client.get("/api/prices", params={"region": "paarl"}).json()["region"] == "paarl"
    assert client.get("/api/health").json() == {"status": "ok", "regions": ["paarl"]}
    assert client.get("/api/regions").json() == ["paarl"]


def test_cors_restricted(client):
    ok = client.get("/api/v1/regions", headers={"Origin": "https://pricepulse.example"})
    assert ok.headers.get("access-control-allow-origin") == "https://pricepulse.example"
    bad = client.get("/api/v1/regions", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in bad.headers


def test_cache_and_gzip(client, settings):
    seed(settings)
    r = client.get("/api/v1/products", headers={"Accept-Encoding": "gzip"})
    assert r.headers["cache-control"].startswith("public, max-age=")
    assert r.headers.get("content-encoding") == "gzip"


def test_rate_limit(settings, monkeypatch):
    settings.rate_limit_per_min = 3
    c = TestClient(create_app(settings))
    codes = [c.get("/api/v1/regions").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_openapi_docs(client):
    assert client.get("/docs").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    for p in [
        "/api/v1/health",
        "/api/v1/regions",
        "/api/v1/products",
        "/api/v1/products/{product_id}",
        "/api/v1/towns/compare",
        "/api/v1/basket/optimise",
        "/api/v1/prices",
    ]:
        assert p in paths


def test_list_page_under_100kb(client, settings):
    extra = [
        row(s, f"Brand{i} Product {i} 1L", 10 + i) for i in range(100) for s in ("Checkers", "Pick n Pay")
    ]
    seed(settings, extra=extra)
    r = client.get("/api/v1/products", params={"limit": 100})
    assert len(r.content) < 100_000
