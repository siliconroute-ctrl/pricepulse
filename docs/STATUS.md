# Status: what works, what is unverified

Date: 27 Oct 2026. Built in a cloud session that **could not reach the retailer
websites**: its network policy refused every store domain (HTTP 403 from the
proxy). Nothing below claims a scraper works on a live site.

## Phase 1: Scrapers

| Item | State |
|---|---|
| Six store scrapers with API-first, page-fallback, JSON-LD fallback | Built |
| Selectors / data-feed URLs | **Unverified guesses.** Tested only on synthetic pages shaped like those guesses |
| `--debug`: first 5 rows; screenshot + page saved to `debug/{store}/` on zero results | Done, and checked with a real browser against a local test site |
| Health report per store (found, not found, errors, suspect prices, BLOCKED) | Done |
| Backoff on 403/429/CAPTCHA, stop after 3 refusals, other stores continue | Done, checked with a real browser |
| `regions.yaml` (7 towns) | Done. Per-store branch/postcode settings **not filled in**: every store is `unknown` (treated as national) until checked |
| `products.txt` with `# category:` headers, 79 terms | Done |
| Promo vs regular price, reject bad rows, idempotent slots | Done, tested |
| Acceptance: `run_scrapers.py --region paarl` gives 15+ products from 4+ stores | **Not met here.** Live run result: all 6 stores "could not open the website (this network does not allow the website)". Needs a run from a machine with internet access |

## Phase 2: Database and matching

| Item | State |
|---|---|
| SQLAlchemy models: store, product, listing, price, scrape_run; full history | Done |
| Migrations (`schema_version` table, append-only steps) | Done |
| Size parser (all formats in the spec), unit prices | Done, tested |
| Matching: brand + name + size, fuzzy fallback, generic commodities, review queue | Done. 57 regression pairs pass |
| `admin.py review` / `report` / `merge` / `ingest` | Done, tested |
| Acceptance: same product at 3 stores = 1 product with 3 prices | Met (tests, and the sample data: 70 listings → 24 products, 100% compared) |

## Phase 3: API

All endpoints in the spec, pydantic models, pagination, region validation,
CORS from env, gzip, Cache-Control, rate limit (60/min per IP), JSON request logs
without IP addresses, `/docs`. **180 tests pass** (`python -m pytest`), including
empty database, unknown and invalid towns, stale data and the 100 KB page limit.

## Phase 4: Web app

Kept as one HTML file (no build step, 17 KB gzipped). Verified by the browser smoke
test (`frontend/tests/run_smoke.py`), which passes:

- loads from the API and shows "Prices updated …"; search "milk" shows 3 stores
- add to list → cheapest single store and split shop shown
- product detail with price-history chart; Towns tab with real per-town totals
- offline: "Showing saved prices from …" banner
- dark theme follows the phone; base text 17 px, prices 35.7 px, smallest tap target 51 px
- no JavaScript errors

Also checked: WCAG contrast of every colour pair (AAA for text, prices and primary
buttons in both themes; "+ List" text and store badges AA or better).

Not tested on a physical Android phone yet; geolocation and "Install app" need HTTPS
and a real device.

## Phase 5: Hosting

| Item | State |
|---|---|
| `Dockerfile.api` | Built and run here (API, web app, sample data all worked). Base image was later pinned to `python:3.12-slim-bookworm`; that exact build is not re-run (Docker Hub rate limit) |
| `Dockerfile.scraper` | **Not built here**: the sandbox blocks Debian's package servers, which Chromium's libraries come from. Python part installs fine |
| `docker-compose.yml`, `.env.example`, `scheduler.py` | Written; scheduler logic not run for a full cycle |
| GitHub Actions workflow | Written, not yet run |
| Public test URL | **Not deployed: needs your hosting decision** (docs/DEPLOY.md) |

## Next steps

1. Run the scrapers from a machine with normal internet (your PC, or the server) and
   fix selectors per store with docs/FIX_A_SCRAPER.md. Expect most stores to need changes.
2. Choose hosting (docs/DEPLOY.md, Option A recommended), deploy, add the uptime monitor.
3. Hand testers docs/TESTER_GUIDE.md with the URL.
