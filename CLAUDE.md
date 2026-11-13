# PricePulse ZA

## Current status
_Last updated 2026-11-13 (evening). Claude: rewrite this section at the end of every work session. Keep it short and plain English._

**Test copy is live** at https://pricepulse.trolleyup.com, with **example (made-up) prices only** for paarl and wellington. Nothing is public to ordinary users yet.

- **Built and tested (254 server tests, 34-step browser test, GitHub tests pass):** sign-in with TrolleyUp's Firebase accounts; one-time age check by South African ID number (number not kept; keyed fingerprint stops second accounts); lookup sessions (45 items, 3 a day free, midnight SA time); prices locked to looked-up items; under-18s never see liquor; R99 monthly plan through PayFast; Send to TrolleyUp (CSV for TrolleyUp's import); Terms and Privacy pages (drafts); delete-my-data; `admin.py purge-sample` removes example prices only.
- **Test server:** Contabo Cloud VPS in Germany (x86, Ubuntu 24.04, 12 GB). Hardened with the owner step by step (key-only login, non-root `deploy` user, ufw, fail2ban, automatic security updates, rebooted). The app runs in Docker (`api` container only) behind Caddy. Claude cannot log in to it: the owner runs every server command, one block at a time. Oracle Cloud was tried and abandoned.
- **Verified by the owner on a phone (2026-11-11):** sign-in worked (method not stated), the ID age check passed (made-up ID 8001015009087), and the exported list imported into TrolleyUp. **Verified from outside by Claude:** public pages, `/api/v1/health`, prices return 401 without sign-in, a fake test token is refused, and Firebase lists pricepulse.trolleyup.com as an authorized domain.
- **Not yet verified:** every store scraper against the live sites; PayFast (sandbox and live); Google sign-in from this address; Delete my data on the live server; port 80 from outside (`curl.exe -I http://pricepulse.trolleyup.com`); the Contabo snapshot; the scraper Docker image.

**Scrapers (branch `claude/scrapers-real`, not merged yet):** the owner's Windows PC is set up (Python 3.13 in `backend\.venv`, Playwright and Chromium, repo at `C:\Users\joao\pricepulse`; Python 3.15 is too new, some packages have no build for it). Routine: `git pull`, run one store with `--limit 3 --debug --no-db --save-fixtures`, send Claude the screenshots, `.network.txt` feeds and `data\latest_paarl.json` from `debug\{store}` and `tests\fixtures\live\{store}`.
- **Checkers works (13 Nov 2026).** Its desktop site is the Sixty60 shop; products load from `/api/catalogue/get-products-filter` after the page loads. The scraper reads that feed: price, special flag (the usual price is NOT given on specials, so none is invented), product link built from article number + unit, brand, barcode (EAN), alcohol flag (becomes Liquor), tobacco skipped, stock, store id. The owner checked prices and links against checkers.co.za while signed out (no account, no cell number) and they matched; a logged-in view with his own address showed the same prices for 3 products, so regions do not show up so far. Anonymous visitors get a default Checkers store (one store id, about 5 m away); the store id is saved with each price. Two different products can share one name (SASKO Low G.I 800g R29.99 and R19.99): the link code tells them apart. A real excerpt of the feed is a test fixture.
- **Never log in as the owner or anyone else, never solve bot checks.** Checkers uses AWS bot protection; a challenge page is reported as BLOCKED. For end users with the Sixty60 app, the plan is a product link ("See your price at Checkers") that opens their own signed-in session; nothing of theirs is ever read. Waiting for the owner to say what his phone does when TrolleyUp's Checkers Search is tapped.
- **Pick n Pay works (13 Nov 2026): the owner checked prices AND links against pnp.co.za and they matched.** The page calls `/pnphybris/v2/pnp-spa/products/search?...&query=<words>&storeCode=WC21` (Hybris). `WC21` is the default store an anonymous visitor gets and the store code is what sets prices, so other regions need another store chosen on the site (not built; the site shows only "Delivery to: Add address", no store name). The scraper reads that feed: shelf price, a real was/now when `oldPrice` is higher, stock, link `/{name-words}/p/{code}`. **Smart Shopper (loyalty card) and combo offers are kept as text in `deal` and never replace the shelf price.** The feed has no barcode and `brandSellerId` is the manufacturer (Tiger Brands), not the brand, so it is not used. A real excerpt is a test fixture.
- **Feed capture:** the recorder reads each JSON answer the moment it arrives (reading later lost some), skips ad/tracking hosts, and keeps non-price feeds short. `json_first = True` on Checkers and Pick n Pay: read the feed before the page layout. Checked in a real browser.
- **Matching, first real two-store comparison:** 7 products matched across Checkers and Pick n Pay; two real misses were fixed ("Sliced", "G.I"/"Gi", "Whole Wheat"/"Wholewheat") and added to `tests/data/match_pairs.csv`. `run_scrapers.py --category "Health & Beauty"` (or Liquor) searches one category only, for Dis-Chem and the liquor stores. Next store: Woolworths.
- **Woolworths reader works (13 Nov 2026), links checked by the owner (4 product pages opened the right product).** The search feed (Constructor.io) gives barcode (13-digit `data.id`), name, link path, was-price (`*_wp`) and three price columns `p10`, `p30`, `p60`. The site disagrees with itself: the search listing shows `p10` (milk 2 L R38.99) but the product page and the Woolworths app show `p30` (R40.99; Ayrshire milk 2 L was R47.99, now R42.99). The owner confirmed the app shows R40.99, so the reader uses `PRICE_COLUMN = "p30"` (saved in store_ref). `p10`/`p60` are probably other areas or fulfilment groups (not built). Woolworths does not sell Albany: test with `--term "white bread 700g"`.
- **Dis-Chem works from the page layout (13 Nov 2026): the owner checked 2 links and both prices.** Panado Tablets 24's was R39.99, now R35.99; Disprin Regular 12's was R27.99, now R25.19 (struck-through was-price, then the price to pay; the site shows an "Instant 10% discount" banner). The scraper's price and promo match the page and the links open the right products. Dis-Chem is a Magento site with no price feed (0 feeds), so this reads the page layout and may break when the page changes. Better Rewards members get a further discount at checkout: not shown, not read. Prices are Single Exit Prices (cash, excluding dispensing fee). Not known: which search term found nothing. Sizes with a backtick (`24\`s`) now parse.
- **Checkers liquor (13 Nov 2026): `--category Liquor` works, 9 prices for 3 terms, 5 prices checked by the owner against product pages (all matched).** E.g. Castle Lite 6 x 500ml R114.99 (page also says "Buy 2 For R170"), 24 x 500ml R389.99, Castle Lager Bottles 6 x 330ml R89.99. The multi-buy text is now kept in `deal` (from `bonusBuys`) and never replaces the shelf price; `forOver18` also makes an item Liquor. Liquor stores plan (owner, 13 Nov): Checkers Bottle Store (done), Pick n Pay Bottle Store (next), SPAR/TOPS (no public price site found: owner to check tops.co.za). Ultra Liquors and Liquorland are on hold, not removed.
- **Pick n Pay liquor works (13 Nov 2026): 9 prices for 3 terms, 4 checked by the owner on product pages, all matched** (Castle Lager 6 x 330ml R89.99, Castle Lite 6 x 330ml R99.99, 24 x 410ml R354.99, 24 x 500ml R389.99). No age page or block appeared for an anonymous visitor (pages say "Not for sale to u18s, proof of age may be required upon delivery"). Same products cost the same at Checkers in these cases. A product found by two searches is now kept once (by link).
- **One file per store:** with `--save-fixtures` the run writes `backend\send_to_claude\{slug}.txt` (results, health, price feeds, other addresses without query strings). The owner's PC froze on the file picker with many files, so ask for this ONE file (plus a screenshot only when nothing was found). `--term "..."` (repeatable) searches typed terms instead of products.txt.
- Guessed internal data-feed addresses are switched off (`use_api = False` on every scraper) until a real run proves them. Pick n Pay, Woolworths and Dis-Chem search addresses match TrolleyUp's confirmed ones; their page layouts are unverified. Ultra Liquors and Liquorland addresses are guesses: the owner must send the real ones.
- Ideas noted, not built: match products by barcode across stores (needs a migration and other stores' feeds); show "Special" on specials; rank search hits by closeness to the term; per-region prices (test first whether they differ).

**Next (owner's plan, 13 Nov evening):** (1) Pick n Pay liquor done; (2) TOPS dropped; (3) one scrape per store for all towns is DECIDED and built (not yet run end to end: first full run is `run_scrapers.py --limit 3 --no-db` with no `--region`); (4) an email alert when a scraper fails (not built); (5) one-command upload of `latest_{town}.json` from the PC, then `admin.py ingest` and `admin.py purge-sample --yes` on the server; (6) PayFast sandbox. Scrapers done and checked by the owner: Checkers (incl. liquor), Pick n Pay, Woolworths, Dis-Chem. Ultra Liquors and Liquorland on hold.

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

## Decisions (made by the owner, 2026-11-13)
- **One scrape per store for all towns:** each store is searched once and its default prices (Checkers default store, Pick n Pay store `WC21`, Woolworths column `p30`) are written for all seven towns (`regions.yaml` says `national`; `run_scrapers.py` now defaults to `--region all` and lists the towns it serves). Per-town prices are a later question.
- **Liquor stores:** Checkers Bottle Store and Pick n Pay Bottle Store only. TOPS at SPAR is left out (no public price site). Ultra Liquors and Liquorland stay in the code but are on hold: left out of every normal run, the scheduler and the health page (`ACTIVE_SCRAPERS` in `stores.py`); `--store "Ultra Liquors"` still works. Default run = Checkers, Pick n Pay, Woolworths, Dis-Chem. **Each store is searched only for what it sells** (`categories` on the scraper: Dis-Chem = Health & Beauty, Woolworths = no Liquor, Checkers and Pick n Pay = everything); `--limit N` now counts per store; typed `--term` words are used as typed. First full test run (13 Nov, 7 towns) worked: stores searched once and reused for the other six towns; Woolworths found 1 of 3 because the first products.txt terms are Albany/brand terms it doesn't sell.

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
