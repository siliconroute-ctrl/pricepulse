# Status: what works, what is unverified

Last updated 10 Nov 2026. Built in cloud sessions that **could not reach the retailer
websites, PayFast or a live Firebase sign-in**. Nothing below claims any of those
work on the real services.

## Accounts, limits, payments and export (10 Nov 2026)

| Item | State |
|---|---|
| Sign-in with TrolleyUp's accounts (Google, email + password) | Built. Token checking tested with a test signing key (expired, wrong project, forged, guest sign-ins all refused). **Not tried on a live address**: needs the address added in Firebase (docs/ACCOUNTS.md) |
| Age check by SA ID number | Working, tested: check digit, real dates, 18th-birthday edge, century. Number never stored; fingerprint blocks a second account |
| Under-18s | Liquor hidden in search, lookups, list and export. Tested |
| Sessions: 45 items, 3 a day free, reset at midnight SA time | Working, tested on the server and in the browser |
| Prices locked to looked-up items | Working. The old whole-catalogue endpoints answer only with `PP_OPEN_PRICES=1` (tests) |
| R99 monthly plan (PayFast) | Built. Signed checkout, payment notices (paid, repeated, forged, wrong amount, cancelled, lapsed, resubscribe) tested with our own signatures. **Not run against PayFast's sandbox.** Cancel-through-API is the least certain part |
| Send to TrolleyUp | Working. File checked with TrolleyUp's own `csv.js` and item rules. **Not tried by hand in the live TrolleyUp app** |
| Terms of Use and Privacy Policy | **Drafts.** Need an attorney's review |
| Delete my data | Working, tested |
| Browser test | 34 checks pass: sign-in, ID check, lookup, list, export, 3-a-day limit, upgrade hand-off, under-18, second account refused, offline, sizes, dark theme |

Changed from the first version: the Data tab, the file loader and the built-in example
prices are gone from the app, because prices are now only shown to signed-in people
for items they look up. `python run_local.py` gives a test copy with example prices.

## Phase 1: Scrapers

| Item | State |
|---|---|
| Six store scrapers with API-first, page-fallback, JSON-LD fallback | Built |
| Checkers | **Verified by the owner (13 Nov 2026):** prices and product links matched checkers.co.za for the products he opened, signed out. Reads Checkers' own product feed (prices, specials, links, barcode, stock) |
| Other five stores | **Unverified.** Selectors and feed addresses are guesses; tested only on synthetic pages. Guessed feed addresses are switched off (`use_api = False`) |
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
without IP addresses, `/docs`. **253 tests pass** (`python -m pytest`), including
empty database, unknown and invalid towns, stale data and the 100 KB page limit.

## Phase 4: Web app

Kept as one HTML file (no build step, 21 KB gzipped). Verified by the browser smoke
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
| `docker-compose.yml`, `.env.example`, `scheduler.py` | Written; scheduler logic not run for a full cycle. The scraper service now only starts with `--profile scraper` |
| Test server (Contabo, Germany) | Hardened (key-only login, non-root user, firewall, fail2ban, automatic security updates, rebooted). **App deployed 11 Nov 2026** (Docker `api` container behind Caddy, valid HTTPS certificate) at https://pricepulse.trolleyup.com with EXAMPLE prices for paarl and wellington |
| GitHub Actions workflow | Runs on every push (ruff, black, 254 tests, browser test). Every run so far has passed |
| Public test URL | https://pricepulse.trolleyup.com (test copy, example prices only). **Verified by the owner on a phone, 11 Nov 2026:** sign-in worked, the ID age check passed (made-up ID), and the list exported with Send to TrolleyUp imported correctly into TrolleyUp. **Not yet tried:** Google sign-in from this address, Delete my data, a PayFast payment, real prices |

## Next steps

1. Run the scrapers from a machine with normal internet (your PC, or the server) and
   fix selectors per store with docs/FIX_A_SCRAPER.md. Expect most stores to need changes.
2. Choose hosting (docs/DEPLOY.md, Option A recommended), deploy, add the uptime monitor.
3. Hand testers docs/TESTER_GUIDE.md with the URL.
