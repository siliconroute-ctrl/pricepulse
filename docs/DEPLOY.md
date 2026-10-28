# Deploying PricePulse ZA for testing

Two ways. **Option A is recommended**: one small Linux server runs everything
(app, API, scheduler) with one command. Option B adds Firebase Hosting for the app,
since you already use it.

> Not yet done: these steps have not been run against a real server or Firebase
> project. They need your accounts. Ask Claude Code to walk through them with you.

## Which host? (decision needed)

| | Option A: small VPS | Option B: Firebase + VPS | Not recommended: Render/Railway free tier |
|---|---|---|---|
| App (web) | served by the API | Firebase Hosting (free) | free static site |
| API | VPS | VPS | sleeps when idle; free tier has **no permanent disk**, so the SQLite database is wiped on each restart |
| Scraper + Chromium | VPS | VPS | needs a paid background worker |
| Cost | about R80-R150 a month | same | free tier not workable for scraping |
| Moving parts | 1 server | server + Firebase | 2-3 services |

A server in **South Africa** (for example xneelo, Afrihost or Hetzner's Johannesburg
location if available) is worth considering: some retailer sites treat foreign or
data-centre traffic with more suspicion. Even so, a store may block a server's
address; the health report will say so.

## Option A: one server with Docker

Needs: a Linux server (Ubuntu 24.04, 1 GB RAM minimum, 2 GB better for Chromium),
a domain or subdomain pointing at it (e.g. `prices.siliconroute.co.za`).

### 1. Install Docker on the server

```bash
ssh root@YOUR_SERVER_IP
curl -fsSL https://get.docker.com | sh
```

### 2. Get the code and settings

```bash
git clone https://github.com/siliconroute-ctrl/pricepulse.git
cd pricepulse
cp backend/.env.example backend/.env
nano backend/.env
```

In `.env` set at least:

```
PP_ALLOWED_ORIGINS=https://prices.siliconroute.co.za
PP_TRUST_PROXY=1
PP_SCRAPE_REGIONS=paarl
```

### 3. Start everything

```bash
docker compose up -d --build
docker compose logs -f scraper      # watch the first price check (Ctrl+C to stop watching)
```

The first price check starts within a minute. Check the API:

```bash
curl http://localhost:8000/api/v1/health
```

### 4. Add HTTPS (needed for "install app" and location on phones)

Install Caddy, which gets a free certificate automatically:

```bash
apt install -y caddy
cat > /etc/caddy/Caddyfile <<'EOF'
prices.siliconroute.co.za {
    reverse_proxy localhost:8000
}
EOF
systemctl reload caddy
```

Open `https://prices.siliconroute.co.za` on your phone. That is the **test URL**.

### 5. Updating later

```bash
cd pricepulse
git pull
docker compose up -d --build
```

### 6. Backups

The database and price files live in the Docker volume `pricepulse_ppdata`.

```bash
docker compose exec api python -c "import sqlite3; s=sqlite3.connect('/data/pricepulse.db'); s.execute(\"VACUUM INTO '/data/backup.db'\")"
docker compose cp api:/data/backup.db ./pricepulse-backup-$(date +%F).db
```

## Option B: app on Firebase Hosting, API on the server

Do Option A steps 1 to 4 first, but use a subdomain like `api.prices.siliconroute.co.za`
for the API. Then on your own computer:

```bash
npm install -g firebase-tools
firebase login
cd pricepulse
```

Tell the app where the API is (this file is the only thing that changes):

**Linux/macOS**
```bash
echo 'window.PRICEPULSE_CONFIG = { apiBase: "https://api.prices.siliconroute.co.za" };' > frontend/config.js
```
**Windows (PowerShell)**
```powershell
Set-Content frontend\config.js 'window.PRICEPULSE_CONFIG = { apiBase: "https://api.prices.siliconroute.co.za" };'
```

Create the Firebase site and deploy:

```bash
firebase projects:create pricepulse-za      # or pick an existing project
firebase use pricepulse-za
firebase deploy --only hosting
```

`firebase.json` is already in this folder. Firebase prints the address, e.g.
`https://pricepulse-za.web.app`. Add that address to `PP_ALLOWED_ORIGINS` in the
server's `.env`, then on the server run `docker compose up -d`.

## Uptime alert (free)

1. Create a free account at https://uptimerobot.com
2. **Add New Monitor** → type **HTTP(s)**.
3. URL: `https://prices.siliconroute.co.za/api/v1/health?strict=1`
4. Interval: 5 minutes. Alert contact: your email.

`?strict=1` makes the health check fail when there has been **no successful scrape
for 24 hours** (setting `PP_ALERT_HOURS`) or when the API is down, so you get an
email in both cases.

## Running without Docker (Linux)

```bash
cd pricepulse/backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-scraper.txt && python -m playwright install --with-deps chromium
python admin.py init-db
nohup uvicorn api:app --host 127.0.0.1 --port 8000 > api.log 2>&1 &
nohup python scheduler.py > scheduler.log 2>&1 &
```

Or use cron instead of `scheduler.py`, staggering stores (`crontab -e`):

```
17 */6 * * * cd /root/pricepulse/backend && .venv/bin/python run_scrapers.py --region paarl --store Checkers
29 */6 * * * cd /root/pricepulse/backend && .venv/bin/python run_scrapers.py --region paarl --store "Pick n Pay"
41 */6 * * * cd /root/pricepulse/backend && .venv/bin/python run_scrapers.py --region paarl --store Woolworths
53 */6 * * * cd /root/pricepulse/backend && .venv/bin/python run_scrapers.py --region paarl --store "Ultra Liquors"
5 1-23/6 * * * cd /root/pricepulse/backend && .venv/bin/python run_scrapers.py --region paarl --store Liquorland
23 1-23/6 * * * cd /root/pricepulse/backend && .venv/bin/python run_scrapers.py --region paarl --store Dis-Chem
```

## Moving to Postgres later

Set `PP_DATABASE_URL=postgresql+psycopg://user:pass@host/pricepulse` and
`pip install "psycopg[binary]"`. Tables are created on first start. Copying existing
SQLite data across is a separate step; ask before doing it, as it is easy to get wrong.
