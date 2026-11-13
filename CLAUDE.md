# PricePulse ZA

## Current status
_Last updated 2026-11-11 (evening). Claude: rewrite this section at the end of every work session. Keep it short and plain English._

**Test copy is live** at https://pricepulse.trolleyup.com, with **example (made-up) prices only** for paarl and wellington. Nothing is public to ordinary users yet.

- **Built and tested (254 server tests, 34-step browser test, GitHub tests pass):** sign-in with TrolleyUp's Firebase accounts; one-time age check by South African ID number (number not kept; keyed fingerprint stops second accounts); lookup sessions (45 items, 3 a day free, midnight SA time); prices locked to looked-up items; under-18s never see liquor; R99 monthly plan through PayFast; Send to TrolleyUp (CSV for TrolleyUp's import); Terms and Privacy pages (drafts); delete-my-data; `admin.py purge-sample` removes example prices only.
- **Test server:** Contabo Cloud VPS in Germany (x86, Ubuntu 24.04, 12 GB). Hardened with the owner step by step (key-only login, non-root `deploy` user, ufw, fail2ban, automatic security updates, rebooted). The app runs in Docker (`api` container only) behind Caddy. Claude cannot log in to it: the owner runs every server command, one block at a time. Oracle Cloud was tried and abandoned.
- **Verified by the owner on a phone (2026-11-11):** sign-in worked (method not stated), the ID age check passed (made-up ID 8001015009087), and the exported list imported into TrolleyUp. **Verified from outside by Claude:** public pages, `/api/v1/health`, prices return 401 without sign-in, a fake test token is refused, and Firebase lists pricepulse.trolleyup.com as an authorized domain.
- **Not yet verified:** every store scraper against the live sites; PayFast (sandbox and live); Google sign-in from this address; Delete my data on the live server; port 80 from outside (`curl.exe -I http://pricepulse.trolleyup.com`); the Contabo snapshot; the scraper Docker image.

**Next (owner's plan):** (1) make the scrapers real **on the owner's Windows PC** (never from the EU server for now): set up Python, git and Playwright there, run one store at a time with `--debug --save-fixtures`, send Claude the saved pages so selectors can be fixed; (2) a one-command upload of `latest_{town}.json` from the PC (key login, no password in scripts), then `admin.py ingest` on the server and `admin.py purge-sample --yes`; (3) PayFast sandbox.

**Waiting on the owner**
- Port 80 check; a Contabo snapshot of the working app; try Google sign-in and Delete my data.
- PayFast sandbox account and test, then a live merchant account with subscriptions enabled (docs/PAYMENTS.md).
- Legal: attorney review of terms.html and privacy.html; register an Information Officer; decide whether under-18s may use the app at all (POPIA guardian consent). Until then only the owner and people they know sign in.
- Confirm the support email (now admin@trolleyup.com, in frontend/config.js and the legal pages).
- Contabo Auto Backup (EUR 3.35 a month): switch on once real accounts exist.

## Working style (owner's rules)
- **Always ask which repo to use before creating a new app.** This repo is for PricePulse only.
- Ask before any decision that is hard to reverse: hosting, paid services, payment or sign-in providers, anything that drops data.
- Explain business implications before implementation detail. Plain language, exact commands and file names.
- Build simple first, automate later. Keep changes small and say what changed.
- Never claim a scraper, payment or sign-in works without running it. If a site blocks access, say so. Never invent prices.
- Ask before deploying or merging to `main`.
- The owner has restricted vision: base text 17px or more, prices 28px or more, tap targets 48px or more, WCAG AA contrast (AAA for prices and main buttons), never colour alone, labels on every icon button.

## Decisions (made by the owner, 2026-11-10)
- **Sign-in:** the same Firebase accounts as TrolleyUp (project `trolleyup-eff50`). PricePulse never reads or writes TrolleyUp's Firestore. TrolleyUp's repo is not changed from here.
- **ID number:** checked on the server, never stored or logged. Stored: adult yes/no, the 18th-birthday date for minors, and an HMAC fingerprint keyed with `PP_ID_SECRET` (one ID, one account).
- **Under 18:** allowed, liquor hidden (catalogue, lookups, list, export).
- **Payments:** PayFast, R99 a month. Play Store billing is a later question (docs/PLAY_STORE_PLAN.md).
- **Limits:** 45 looked-up items per session; removing an item does not free its slot; 3 sessions a day free; paid = unlimited sessions.
- **TrolleyUp export:** a CSV file for TrolleyUp's existing import. A direct send would need TrolleyUp rule changes; not built.

## Decisions (made by the owner, 2026-11-11)
- **Regions:** keep the seven Western Cape towns as they are (Paarl default). Do not add other provinces for now.
- **Server:** Docker (not systemd) for the API; Caddy on the host gives HTTPS. The API is at `/api` on the same address, not at api.trolleyup.com.
- **No static-page stage:** go straight to the API; never publish the JSON price files, because they would show every price without sign-in.
- **Scrapers** run on the owner's PC first. The server's compose file starts only `api` unless `--profile scraper` is given.
- **Secrets** (server passwords, `PP_ID_SECRET`, keys) are never written to files, git or chat.

## Layout
- `backend/pricepulse/`: `scrapers/`, `api/` (`app.py` engine and open testing endpoints, `account_routes.py` signed-in endpoints), `accounts.py` (limits), `said.py` (ID numbers), `auth.py` (Firebase tokens), `payfast.py`, `trolleyup.py` (export), `db.py`, `matching.py`, `sizes.py`.
- `frontend/`: `index.html` (whole app, no build step), `config.js`, `terms.html`, `privacy.html`, `sw.js`.
- `docs/`: STATUS, ACCOUNTS, PAYMENTS, DEPLOY, FIX_A_SCRAPER, TESTER_GUIDE, PLAY_STORE_PLAN.

## Checks before any push
- `cd backend && python -m pytest && ruff check . && black --check .`
- `python frontend/tests/run_smoke.py` (needs Node and Playwright).
- When Terms or Privacy wording changes, raise `terms_version` in `backend/pricepulse/config.py` and update both pages.
- Bump `VERSION` in `frontend/sw.js` with every app release.
- `PP_AUTH_DEV` and `PP_OPEN_PRICES` are for tests and `run_local.py` only. The Docker image refuses to start with either on.
