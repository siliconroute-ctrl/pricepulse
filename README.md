# PricePulse ZA

Price comparison for South African shoppers. Search a product, see it priced across
Checkers, Pick n Pay, Woolworths, Ultra Liquors, Liquorland and Dis-Chem, see which
town is cheapest, and build a shopping list that shows where to shop.

It is not a shop and takes no orders. Free for 3 lookup sessions a day; R99 a month for unlimited.

> **Prices are collected from public retailer websites and may differ in store.
> Always check at the till.**

## Current status (read this first)

| Part | State |
|---|---|
| Store scrapers | Built and tested on **synthetic** saved pages. **Not yet checked against the live websites.** See [docs/FIX_A_SCRAPER.md](docs/FIX_A_SCRAPER.md). |
| Accounts, age check, sessions and limits | Working and tested. Sign-in uses TrolleyUp's accounts. |
| R99 monthly plan (PayFast) | Built and tested against our own checks. **Not yet run against PayFast.** See [docs/PAYMENTS.md](docs/PAYMENTS.md). |
| Send to TrolleyUp | Working. The file was checked against TrolleyUp's own import code. |
| Web app (installable) | Working. 34-step browser test passes. |
| Public address | Not deployed yet. See [docs/DEPLOY.md](docs/DEPLOY.md). |

Full detail, including what is unverified: [docs/STATUS.md](docs/STATUS.md).

## How the app works for a user

1. Sign in with Google or email (the same account as TrolleyUp).
2. Confirm age once with a South African ID number. The number is not kept.
   Under-18s do not see liquor.
3. Search, then tap **Look up price** on an item to compare stores. A session holds
   45 items.
4. The List tab shows the cheapest single store and the cheapest split shop.
   **Send to TrolleyUp** exports the list.
5. **Clear session** starts again. Free: 3 sessions a day. **R99 a month**: unlimited.

Rules and settings: [docs/ACCOUNTS.md](docs/ACCOUNTS.md).

## What is where

```
pricepulse/
  backend/
    run_scrapers.py      run the store scrapers (one command)
    scheduler.py         run them every 6 hours
    api.py               the API (uvicorn api:app)
    admin.py             database, match report, review queue
    make_sample.py       EXAMPLE data for testing without scraping
    products.txt         what to search for, with "# category:" headers
    regions.yaml         towns, and how each store picks a branch
    pricepulse/          the code: scrapers/, api/, db.py, matching.py, ...
    tests/               pytest suite and saved pages (fixtures)
  frontend/
    index.html           the whole web app (no build step)
    config.js            where the app finds the API
    sw.js, manifest.webmanifest, icons/   makes it installable and work offline
    tests/               browser smoke test
  docs/                  deploy, testers, fixing scrapers, Google Play plan
  Dockerfile.api, Dockerfile.scraper, docker-compose.yml
```

## Install

You need Python 3.11 or newer. For the browser smoke test you also need Node.js 18+.

**Linux**

```bash
cd pricepulse/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt -r requirements-scraper.txt
python -m playwright install --with-deps chromium
cp .env.example .env
```

**Windows (PowerShell)**

```powershell
cd pricepulse\backend
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt -r requirements-scraper.txt
python -m playwright install chromium
copy .env.example .env
```

If PowerShell refuses to run `Activate.ps1`, run once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## Run

All commands are run from `pricepulse/backend` with the virtual environment active.

| What | Command |
|---|---|
| Try the whole app with example prices and a test sign-in | `python run_local.py`, open http://127.0.0.1:8000 |
| Scrape real prices for Paarl | `python run_scrapers.py --region paarl` |
| One store, with details | `python run_scrapers.py --region paarl --store Checkers --debug` |
| Every town | `python run_scrapers.py --region all` |
| Keep scraping every 6 hours | `python scheduler.py` |
| Match report | `python admin.py report` |
| Accounts and subscribers | `python admin.py users` |
| Give someone the Unlimited plan | `python admin.py plan <account-id> paid` |
| Decide uncertain matches | `python admin.py review` |
| Load a JSON file into the database | `python admin.py ingest data/latest_paarl.json` |
| Tests | `python -m pytest` |
| Browser smoke test | `python ../frontend/tests/run_smoke.py` |
| Code style | `ruff check .` and `black --check .` |

API documentation is at http://127.0.0.1:8000/docs while the API runs.

Example prices from `make_sample.py` are made up. They are flagged as sample
everywhere, and the app shows an "Example prices" banner for them. To start clean,
delete `backend/data/pricepulse.db` and the `latest_*.json` files.

### What a scrape prints

```
PricePulse scrape for Paarl - 27 Oct 2026, 07:30
  Checkers: searching 79 products...
  Checkers: 61 of 79 products found, 18 not found.
  Woolworths: 0 of 79 products found, 79 not found. BLOCKED by the website - stopped this store and moved on.
  Woolworths: kept 52 prices from the last good run.
Summary for paarl: 240 prices from 5 stores.
```

It writes `data/latest_paarl.json` (the file the app can load), a dated copy in
`data/history/`, a health report `data/health_paarl.json`, and saves everything in
the database. If one store fails, the others carry on and the last good prices for
the failed store are kept.

## Deploy

See [docs/DEPLOY.md](docs/DEPLOY.md) for step-by-step commands. In short:
`docker compose up -d --build` on a small Linux server gives the app, the API
and the scheduler in one go.

## Troubleshooting

| Problem | What to do |
|---|---|
| `Playwright is not installed` | `pip install -r requirements-scraper.txt` then `python -m playwright install chromium` |
| A store finds 0 products | Run it alone with `--debug` and open `debug/{store}/`: there is a screenshot and the page. Follow [docs/FIX_A_SCRAPER.md](docs/FIX_A_SCRAPER.md). |
| A store says BLOCKED | The website refused automated access. Do not try to get around it. Wait a day; if it continues, leave that store out and contact the retailer. |
| App says "Example prices" | The server only has sample data. Run the scraper. |
| Sign-in fails on the live address | Add the address to Firebase's Authorized domains: [docs/ACCOUNTS.md](docs/ACCOUNTS.md). |
| Paid but still shows Free | Look for the outcome word in the logs: [docs/PAYMENTS.md](docs/PAYMENTS.md). |
| Server will not start: "Refusing to start" | Set `PP_ID_SECRET`, and keep `PP_AUTH_DEV=0` and `PP_OPEN_PRICES=0` in `.env`. |
| App says "Showing saved prices from …" | The phone is offline or the API is down. It shows the last prices it saw. |
| `Unknown region` | Use a name from `regions.yaml`, e.g. `paarl`, `cape-town`. |
| Browser shows a CORS error | Add the app's address to `PP_ALLOWED_ORIGINS` in `.env` and restart the API. |
| `database is locked` | Two scrapes ran at once. Let one finish; the scheduler never runs two together. |
| Windows: `uvicorn` not found | Activate the virtual environment first: `.venv\Scripts\Activate.ps1` |

## Before scaling or making money from this

Review each retailer's website terms of use before scaling up or adding affiliate
links or ads. Some retailers forbid automated collection; if one does, remove it or
ask for permission or a data feed. The scrapers are deliberately slow and polite
(1.5 to 3.5 seconds between requests, one store at a time, they back off when
refused, no logins, no CAPTCHA solving), but that does not replace permission.

## Privacy

PricePulse holds personal information, so POPIA applies: see
[docs/ACCOUNTS.md](docs/ACCOUNTS.md). In short: the ID number is checked and thrown
away (only 18+ yes/no and a keyed fingerprint are kept); no names, emails or card
numbers are stored on the PricePulse server; location stays on the phone; API logs
hold no IP addresses; people can delete their own data in the app.
