# Hands-off prices: the Linux scraper machine and the web upload

The always-on Linux desktop scrapes every night and uploads the prices to the server over HTTPS.
**It never needs a sudo password, a server login or a key passphrase.** The one stored credential
is an upload token that can only load price files.

```
Linux desktop (home, South Africa)                     Contabo server
  nightly.sh  ->  run_scrapers.py  (4 stores)
              ->  push_prices.py  --- HTTPS + token --->  POST /api/v1/ingest/prices
                                                          (loads prices, records a run per store)
```

## What the token can and cannot do
- It can load one price file per request (the `latest_{town}.json` format). Nothing else.
- It cannot read accounts, age checks or payments, and it is not a sign-in token.
- With no `PP_INGEST_TOKEN` set on the server the address does not exist (404).
- Five wrong tokens from one address in 10 minutes block that address for a while.
- Files must name a known town, have a date, be under 72 hours old, not be example data, be under
  20 MB and 30 000 rows.
- To cancel it: delete the `PP_INGEST_TOKEN` line on the server and recreate the app. To change it:
  put a new value on the server and in the desktop file.

## 1. Server: switch the upload on (once, after the code is deployed)
The token is made on the server and is never shown in chat. Connect to the server, then:

```
cd ~/pricepulse
grep -q '^PP_INGEST_TOKEN=' backend/.env || echo "PP_INGEST_TOKEN=$(openssl rand -hex 32)" >> backend/.env
grep -c '^PP_INGEST_TOKEN=' backend/.env
sudo docker compose up -d --force-recreate api
sudo docker compose logs api --tail 5
```

The count must print `1`. To copy the token to the desktop, show it on the server's screen only:
`grep '^PP_INGEST_TOKEN=' backend/.env` (do not paste it anywhere).

## 2. Linux desktop: store the token (once)
In a terminal on the desktop. Paste the token when asked (nothing is shown as you paste):

```
mkdir -p ~/.config/pricepulse
umask 077
read -rs -p "Paste the token, then press Enter: " T; echo
printf '%s\n' "$T" > ~/.config/pricepulse/upload_token
unset T
ls -l ~/.config/pricepulse/upload_token
```

The listing must start with `-rw-------` (only you can read it).

## 3. Linux desktop: the scraper tools (once)
See the step-by-step in the conversation: system packages, a read-only GitHub key, the code in
`~/pricepulse`, the Python environment (`backend/.venv`) and Chromium for Playwright. Then test:

```
cd ~/pricepulse/backend
.venv/bin/python run_scrapers.py --store Checkers --limit 3 --no-db
```

## 4. Alert email on the desktop (optional but recommended)
A file only you can read, `~/.config/pricepulse/env`, loaded by the night run:

```
export PP_ALERT_SMTP_HOST=smtp.trolleyup.com
export PP_ALERT_SMTP_PORT=465
export PP_ALERT_SMTP_USER=admin@trolleyup.com
export PP_ALERT_SMTP_PASSWORD='the mailbox password'
export PP_ALERT_TO=the-address-that-should-get-alerts
```

Run `chmod 600 ~/.config/pricepulse/env` after creating it. A mailbox used only for alerts is safest.

## 5. The nightly timer (once)
```
cd ~/pricepulse
scripts/linux/install_timer.sh
sudo loginctl enable-linger "$USER"
```
The run starts at 02:30 (the desktop's own clock: check with `timedatectl` that it shows South
Africa time). If the machine was off at 02:30 it runs as soon as it is back on. Logs are in
`~/pricepulse-logs/`. Run it by hand any time with `scripts/linux/nightly.sh`, or
`systemctl --user start pricepulse-nightly.service`.

Also set the machine never to suspend (Settings, Power) and to start again after a power cut
(BIOS setting "Restore on AC power loss").

## 6. Be told when it stops (free, no code)
If the desktop dies, nothing runs, so nothing can send an alert. Watch the server from outside:
a free uptime monitor (for example UptimeRobot) checking
`https://pricepulse.trolleyup.com/api/v1/health?strict=1` every 30 minutes. That address answers
with an error whenever the newest prices are more than 24 hours old, and the monitor emails you.
