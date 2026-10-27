"""PricePulse ZA API. Run with:  uvicorn api:app --reload

Versioned endpoints live under /api/v1. The original /api/health, /api/regions and
/api/prices stay for the file bridge.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque
from datetime import timedelta

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..basket import optimise
from ..config import Settings, get_settings
from ..db import Listing, Price, Product, Store, session_factory, utcnow
from ..money import from_cents
from ..queries import ProductPrices, last_success_per_store, latest_prices, regions_with_data
from ..regions import haversine_km, load_regions, valid_region
from ..scrapers.stores import ALL_SCRAPERS
from . import schemas as S

log = logging.getLogger("pricepulse.api")


class JsonFormatter(logging.Formatter):
    """One JSON object per log line, for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        data.update(getattr(record, "extra_fields", {}))
        return json.dumps(data)


def _setup_logging() -> None:
    if not log.handlers:
        h = logging.StreamHandler()
        h.setFormatter(JsonFormatter())
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False


class RateLimiter:
    """Sliding one-minute window per client IP, kept in memory."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False, int(60 - (now - q[0])) + 1
        q.append(now)
        if len(self.hits) > 10000:  # keep memory bounded
            for k in [k for k, v in self.hits.items() if not v][:5000]:
                del self.hits[k]
        return True, 0


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Tests pass their own settings (temporary database)."""
    settings = settings or get_settings()
    _setup_logging()
    Session = session_factory(settings.database_url)
    regions = load_regions(str(settings.regions_file))
    limiter = RateLimiter(settings.rate_limit_per_min)
    verified = {c.name: c.verified for c in ALL_SCRAPERS}

    app = FastAPI(
        title="PricePulse ZA API",
        version="1.0.0",
        description="Compare grocery, liquor and pharmacy prices across South African stores. "
        "Prices are collected from public retailer websites and may differ in store. "
        "Always check at the till.",
        docs_url="/docs",
    )
    app.add_middleware(GZipMiddleware, minimum_size=500)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
        max_age=3600,
    )

    @app.middleware("http")
    async def guard(request: Request, call_next):  # noqa: ANN001, ANN202
        start = time.perf_counter()
        ip = request.client.host if request.client else "unknown"
        if settings.trust_proxy and request.headers.get("x-forwarded-for"):
            ip = request.headers["x-forwarded-for"].split(",")[0].strip()
        ok, retry = limiter.allow(ip)
        if not ok:
            response = JSONResponse(
                {"detail": "Too many requests. Please wait a minute and try again."},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )
        else:
            response = await call_next(request)
            if request.method == "GET" and response.status_code == 200:
                path = request.url.path
                if path.endswith(("sw.js", "config.js")) or path in ("/", "/index.html"):
                    # The app shell must update promptly; the service worker handles offline.
                    response.headers["Cache-Control"] = "no-cache"
                else:
                    response.headers.setdefault("Cache-Control", f"public, max-age={settings.cache_max_age}")
        # No IP addresses in logs: no personal data is stored.
        log.info(
            "request",
            extra={
                "extra_fields": {
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "ms": round((time.perf_counter() - start) * 1000, 1),
                }
            },
        )
        return response

    def db() -> Session:
        s = Session()
        try:
            yield s
        finally:
            s.close()

    def check_region(region: str) -> str:
        if not valid_region(region):
            raise HTTPException(422, "Region names use only a-z, 0-9, - and _ (up to 40 characters).")
        if region not in regions.regions:
            raise HTTPException(
                404, f"We don't know the town '{region}'. Try one of: " + ", ".join(regions.regions) + "."
            )
        return region

    stale_after = timedelta(hours=settings.stale_hours)

    def product_out(pp: ProductPrices) -> S.ProductOut:
        p = pp.product
        size = pp.size()
        now = utcnow()
        prices = []
        for sp in pp.prices:
            up = size.unit_price(from_cents(sp.effective_cents)) if size else None
            prices.append(
                S.PriceOut(
                    store=sp.store,
                    price=from_cents(sp.price_cents),
                    promo_price=from_cents(sp.promo_cents),
                    url=sp.url,
                    scraped_at=sp.scraped_at,
                    stale=now - sp.scraped_at > stale_after,
                    unit_price=S.UnitPrice(value=up[0], unit=up[1]) if up else None,
                )
            )
        best = pp.best
        up = size.unit_price(from_cents(best.effective_cents)) if (size and best) else None
        return S.ProductOut(
            id=p.id,
            name=p.name,
            brand=p.brand,
            size=p.size_text,
            category=p.category,
            unit_price=S.UnitPrice(value=up[0], unit=up[1]) if up else None,
            prices=prices,
            best_store=best.store if best else None,
            best_price=from_cents(best.effective_cents) if best else None,
        )

    v1 = "/api/v1"

    @app.get(v1 + "/health", response_model=S.HealthOut, tags=["status"])
    def health(
        strict: bool = Query(False, description="Return HTTP 503 when data is stale (for uptime monitors)"),
        s: Session = Depends(db),
    ):  # noqa: B008
        last = last_success_per_store(s)
        with_data = sorted(regions_with_data(s))
        stores = []
        for cls in ALL_SCRAPERS:
            run = last.get(cls.name)
            stores.append(
                S.StoreStatus(
                    store=cls.name,
                    last_success=run.finished_at if run else None,
                    status=run.status if run else "never",
                    item_count=run.item_count if run else 0,
                    verified=verified.get(cls.name, False),
                )
            )
        newest = max((r.finished_at for r in last.values() if r.finished_at), default=None)
        if newest is None:
            status, msg = "empty", "No prices have been collected yet."
        elif utcnow() - newest > timedelta(hours=settings.alert_hours):
            status, msg = (
                "stale",
                f"The last successful price check was over {settings.alert_hours} hours ago.",
            )
        else:
            status, msg = "ok", "Prices are up to date."
        body = S.HealthOut(status=status, message=msg, stores=stores, regions_with_data=with_data)
        if strict and status != "ok":
            return JSONResponse(body.model_dump(mode="json"), status_code=503)
        return body

    @app.get(v1 + "/regions", response_model=list[S.RegionOut], tags=["regions"])
    def list_regions(s: Session = Depends(db)):  # noqa: B008
        have = regions_with_data(s)
        return [
            S.RegionOut(
                slug=r.slug,
                name=r.name,
                lat=r.lat,
                lon=r.lon,
                has_data=r.slug in have,
                scraped_at=have.get(r.slug),
            )
            for r in regions.regions.values()
        ]

    @app.get(v1 + "/products", response_model=S.ProductList, tags=["products"])
    def products(
        region: str = Query("paarl", description="Town, e.g. paarl"),
        q: str | None = Query(None, max_length=80, description="Search words"),
        category: str | None = Query(None, max_length=40),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        s: Session = Depends(db),  # noqa: B008
    ):
        check_region(region)
        grouped = latest_prices(s, region, q=q, category=category)
        rows = sorted(grouped.values(), key=lambda pp: (pp.product.category, pp.product.name.lower()))
        page = rows[offset : offset + limit]
        newest = max((sp.scraped_at for pp in grouped.values() for sp in pp.prices), default=None)
        sample = bool(grouped) and all(sp.is_sample for pp in grouped.values() for sp in pp.prices)
        note = (
            ""
            if grouped
            else (
                "No prices for this town yet."
                if not q and not category
                else "No products match. Try a shorter search."
            )
        )
        return S.ProductList(
            region=region,
            total=len(rows),
            limit=limit,
            offset=offset,
            scraped_at=newest,
            sample=sample,
            note=note,
            items=[product_out(pp) for pp in page],
        )

    @app.get(v1 + "/products/{product_id}", response_model=S.ProductDetail, tags=["products"])
    def product_detail(product_id: int, region: str = Query("paarl"), s: Session = Depends(db)):  # noqa: B008
        check_region(region)
        product = s.get(Product, product_id)
        if product is None:
            raise HTTPException(404, "That product was not found. It may have been merged with another.")
        grouped = latest_prices(s, region, product_ids=[product_id])
        pp = grouped.get(product_id) or ProductPrices(product=product)
        base = product_out(pp)
        cutoff = utcnow() - timedelta(days=90)
        rows = s.execute(
            select(Store.name, Price.scraped_at, Price.price_cents, Price.promo_cents)
            .join(Listing, Listing.id == Price.listing_id)
            .join(Store, Store.id == Listing.store_id)
            .where(Listing.product_id == product_id, Price.region == region, Price.scraped_at >= cutoff)
            .order_by(Price.scraped_at)
        ).all()
        per_store: dict[str, dict[str, float]] = {}
        for store, at, cents, promo in rows:
            day = at.strftime("%Y-%m-%d")
            value = from_cents(promo if promo is not None else cents)
            day_map = per_store.setdefault(store, {})
            day_map[day] = min(value, day_map.get(day, value))  # cheapest of the day
        history = [
            S.StoreHistory(store=st, points=[S.HistoryPoint(date=d, price=v) for d, v in sorted(m.items())])
            for st, m in sorted(per_store.items())
        ]
        return S.ProductDetail(**base.model_dump(), region=region, history=history)

    @app.get(v1 + "/towns/compare", response_model=S.TownsOut, tags=["towns"])
    def towns_compare(
        region: str = Query("paarl"),
        radius_km: float = Query(50, gt=0, le=500),
        product_ids: str | None = Query(
            None, max_length=2000, description="Comma-separated ids; empty = all"
        ),
        s: Session = Depends(db),  # noqa: B008
    ):
        check_region(region)
        ids: list[int] | None = None
        if product_ids:
            try:
                ids = [int(x) for x in product_ids.split(",") if x.strip()][:200]
            except ValueError:
                raise HTTPException(422, "product_ids must be numbers separated by commas.") from None
        home = regions.regions[region]
        if ids is None:
            ids = list(latest_prices(s, region).keys())
        towns = []
        for r in regions.regions.values():
            km = haversine_km(home.lat, home.lon, r.lat, r.lon)
            if km > radius_km:
                continue
            grouped = latest_prices(s, r.slug, product_ids=ids) if ids else {}
            total = sum(pp.best.effective_cents for pp in grouped.values() if pp.best)
            newest = max((sp.scraped_at for pp in grouped.values() for sp in pp.prices), default=None)
            towns.append(
                S.TownOut(
                    region=r.slug,
                    name=r.name,
                    distance_km=round(km, 1),
                    total=from_cents(total) if grouped else None,
                    items_priced=len(grouped),
                    items_missing=len(ids) - len(grouped),
                    scraped_at=newest,
                )
            )
        towns.sort(key=lambda t: (t.total is None, t.items_missing, t.total or 0, t.distance_km))
        with_data = [t for t in towns if t.total is not None]
        note = ""
        if not ids:
            note = "No products to compare yet."
        elif len(with_data) <= 1:
            note = (
                "Only one town has prices so far, so towns cannot be compared yet."
                if with_data
                else "No town in this radius has prices yet."
            )
        elif any(t.items_missing for t in with_data):
            note = "Some towns are missing some items; those totals are lower because of that."
        return S.TownsOut(region=region, radius_km=radius_km, product_count=len(ids), note=note, towns=towns)

    @app.post(v1 + "/basket/optimise", response_model=S.BasketOut, tags=["basket"])
    def basket(body: S.BasketIn, s: Session = Depends(db)):  # noqa: B008
        check_region(body.region)
        qty: dict[int, int] = {}
        for it in body.items:
            qty[it.product_id] = qty.get(it.product_id, 0) + it.quantity
        grouped = latest_prices(s, body.region, product_ids=list(qty))
        prices = {pid: {sp.store: sp.effective_cents for sp in pp.prices} for pid, pp in grouped.items()}
        res = optimise(qty, prices)
        names = {
            pid: pp.product.name + (f" {pp.product.size_text}" if pp.product.size_text else "")
            for pid, pp in grouped.items()
        }

        def st(t):  # noqa: ANN001, ANN202
            return S.StoreTotalOut(
                store=t.store,
                total=from_cents(t.total_cents),
                items_found=t.items_found,
                full_coverage=t.full_coverage,
                missing_product_ids=t.missing,
            )

        newest = max((sp.scraped_at for pp in grouped.values() for sp in pp.prices), default=None)
        return S.BasketOut(
            region=body.region,
            cheapest_single_store=st(res.single_store) if res.single_store else None,
            stores=[st(t) for t in res.stores],
            split=[
                S.SplitLineOut(
                    product_id=line.product_id,
                    name=names.get(line.product_id, ""),
                    store=line.store,
                    quantity=line.quantity,
                    unit_price=from_cents(line.unit_cents),
                    line_total=from_cents(line.line_cents),
                )
                for line in res.split
            ],
            split_total=from_cents(res.split_total_cents),
            split_saving=from_cents(res.split_saving_cents),
            not_found_product_ids=res.not_found,
            scraped_at=newest,
        )

    def prices_file(region: str):  # noqa: ANN202
        if not valid_region(region):
            raise HTTPException(422, "Region names use only a-z, 0-9, - and _ (up to 40 characters).")
        path = settings.data_dir / f"latest_{region}.json"
        if not path.is_file():
            raise HTTPException(404, f"No price file for '{region}' yet. Run the scraper first.")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.get(v1 + "/prices", tags=["file bridge"])
    def prices_v1(region: str = Query("paarl")):
        """The raw latest_{region}.json scraper file (backward-compatible contract)."""
        return prices_file(region)

    # Original endpoints, kept for backward compatibility.
    @app.get("/api/health", include_in_schema=False)
    def legacy_health():
        files = sorted(p.stem.removeprefix("latest_") for p in settings.data_dir.glob("latest_*.json"))
        return {"status": "ok", "regions": files}

    @app.get("/api/regions", include_in_schema=False)
    def legacy_regions():
        return sorted(p.stem.removeprefix("latest_") for p in settings.data_dir.glob("latest_*.json"))

    @app.get("/api/prices", include_in_schema=False)
    def legacy_prices(region: str = Query("paarl")):
        return prices_file(region)

    # Serve the web app from the same address when the folder is present, so one
    # service gives one test URL. It can also be hosted separately (see docs/DEPLOY.md).
    if settings.frontend_dir.is_dir() and (settings.frontend_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="web")

    return app
