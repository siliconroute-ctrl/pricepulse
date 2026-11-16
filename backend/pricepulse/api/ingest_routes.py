"""Price upload from the scraper machine: one private address, one purpose.

``POST /api/v1/ingest/prices`` takes one ``latest_{town}.json`` file as the request body and
loads it exactly like ``admin.py ingest``. It is protected by a single long random token
(``PP_INGEST_TOKEN``) and can do nothing else: it cannot read accounts or change anything but
prices. With no token set it does not exist (404).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections import defaultdict, deque
from datetime import timedelta

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..db import utcnow
from ..ingest import ingest_items, parse_scraped_at, read_scraper_json, record_runs, rematch
from ..regions import valid_region

log = logging.getLogger("pricepulse.ingest_api")

MAX_AGE = timedelta(hours=72)  # an old file must not overwrite newer prices
MAX_FUTURE = timedelta(hours=24)
BAD_TRIES = 5  # wrong tokens allowed per address ...
BAD_WINDOW = 600  # ... in this many seconds


def _digest(text: str) -> bytes:
    return hashlib.sha256(text.encode("utf-8")).digest()


def register_ingest_routes(app: FastAPI, ctx) -> None:  # noqa: ANN001
    settings = ctx.settings
    bad: dict[str, deque[float]] = defaultdict(deque)

    def client_ip(request: Request) -> str:
        ip = request.client.host if request.client else "unknown"
        if settings.trust_proxy and request.headers.get("x-forwarded-for"):
            ip = request.headers["x-forwarded-for"].split(",")[0].strip()
        return ip

    def check_token(request: Request, authorization: str = Header(default="")) -> None:
        if not settings.ingest_token:
            raise HTTPException(404, "Not found")
        ip = client_ip(request)
        now = time.monotonic()
        q = bad[ip]
        while q and now - q[0] > BAD_WINDOW:
            q.popleft()
        if len(q) >= BAD_TRIES:
            raise HTTPException(429, "Too many wrong tokens. Try again later.")
        given = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        if not hmac.compare_digest(_digest(given), _digest(settings.ingest_token)):
            q.append(now)
            log.warning("Price upload refused: wrong or missing token from %s", ip)
            raise HTTPException(401, "Wrong or missing upload token.")

    @app.post("/api/v1/ingest/prices", include_in_schema=False, dependencies=[Depends(check_token)])
    async def ingest_prices(request: Request, s: Session = Depends(ctx.db)):  # noqa: B008
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > settings.ingest_max_bytes:
            raise HTTPException(413, "That file is too large.")
        body = await request.body()
        if len(body) > settings.ingest_max_bytes:
            raise HTTPException(413, "That file is too large.")
        try:
            data = json.loads(body)
            region, stamp, items = read_scraper_json(data)
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(422, "That is not a price file (JSON with an 'items' list).") from exc
        if isinstance(data, dict) and data.get("sample"):
            raise HTTPException(422, "Example (made-up) price files are not accepted here.")
        if not valid_region(region) or region not in ctx.regions.regions:
            raise HTTPException(422, "The file names no known town.")
        if not stamp:
            raise HTTPException(422, "The file has no scraped_at date.")
        when = parse_scraped_at(stamp)
        now = utcnow()
        if now - when > MAX_AGE:
            raise HTTPException(422, "That file is more than 72 hours old, so it was not loaded.")
        if when - now > MAX_FUTURE:
            raise HTTPException(422, "The file's date is in the future.")
        if not items or len(items) > settings.ingest_max_items or not all(isinstance(r, dict) for r in items):
            raise HTTPException(422, "The file has no prices, or too many rows.")

        def load():  # runs off the main loop so other visitors are not held up
            rep = ingest_items(s, items, region, when, settings)
            record_runs(s, items, region, when)
            joined = 0
            if settings.auto_rematch:  # apply the newest matching rules to everything loaded so far
                joined = rematch(s, settings).joined
            s.commit()
            return rep, joined

        report, rejoined = await run_in_threadpool(load)
        log.info("Price upload loaded: %s %s", region, report.summary())
        return {
            "region": region,
            "rows": report.rows,
            "saved": report.prices_written,
            "matched": report.matched,
            "new_products": report.new_products,
            "needs_review": report.needs_review,
            "by_barcode": report.by_barcode,
            "rejected": len(report.rejected),
            "rematched": rejoined,
        }
