# PricePulse ZA

## Current status
_Last updated 2026-11-10. Claude: rewrite this section at the end of every work session. Keep it short and plain English._

**Not live yet.** Nothing is deployed and no hosting has been chosen.

- **Built and tested (253 server tests, 34-step browser test):** sign-in with TrolleyUp's Firebase accounts; one-time age check by South African ID number (number not kept; keyed fingerprint stops second accounts); lookup sessions (45 items, 3 a day free, midnight SA time); prices locked to looked-up items; under-18s never see liquor; R99 monthly plan through PayFast; Send to TrolleyUp (CSV for TrolleyUp's import); Terms and Privacy pages (drafts); delete-my-data.
- **Unverified (could not be reached from the build machine):** every store scraper against the live sites; PayFast (sandbox and live); Firebase sign-in on a real address; the scraper Docker image.
- The accounts, limits and billing work was merged into `main` on 2026-11-10 (owner said "merge"). Merging deploys nothing: there is no hosting yet.

**Waiting on the owner**
- Choose hosting (docs/DEPLOY.md, Option A recommended) so there is a public address.
- Run the scrapers from a machine with normal internet, or allow the six store domains in the cloud environment, then fix selectors (docs/FIX_A_SCRAPER.md).
- Firebase: add the PricePulse address to Authorized domains (docs/ACCOUNTS.md).
- PayFast: sandbox account and test, then a live merchant account with subscriptions enabled (docs/PAYMENTS.md).
- Legal: attorney review of terms.html and privacy.html; register an Information Officer; decide whether under-18s may use the app at all (POPIA guardian consent).
- Confirm the support email (now admin@trolleyup.com, in frontend/config.js and the legal pages).

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
