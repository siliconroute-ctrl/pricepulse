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


## Google Drive folder for essential items (hands-off)
Drop a CSV into one Google Drive folder and it is imported by itself at the next 02:30 night run
(or at once with `pp essentials`). The desktop reads the folder with **rclone** (a free, well-known
sync tool). The CSV needs a column `item` (or the items in the first column) and may have a column
`category` (Groceries, Fresh, Health & Beauty, Liquor). Items already on the list are skipped, and a
file that has not changed is not read again.

**What you are allowing.** Google cannot give a tool access to one folder only, so the sign-in
lets rclone *read* (never change or delete) your Drive. rclone is then pointed at your one folder and
only copies CSV files from it. The sign-in is stored on the desktop in `~/.config/rclone/rclone.conf`
(keep it private: `chmod 600`). You can cancel it any time at https://myaccount.google.com/permissions.

**One-time setup, on the Linux desktop (one block at a time):**
1. In Google Drive, make a folder, for example `PricePulse essentials`. Open it and copy the long
   code at the end of the address (after `/folders/`): that is the folder ID.
2. Install rclone: `sudo apt install rclone`
3. Connect it: `rclone config`, then answer: `n` (new remote), name `gdrive`, storage type `drive`,
   client_id Enter, client_secret Enter, scope `2` (read-only), service account Enter, advanced
   config `n`, web browser `y` (a browser opens: sign in and allow read-only), shared drive `n`,
   keep this remote `y`, then `q`.
4. Point it at your folder: `rclone config update gdrive root_folder_id "THE-FOLDER-ID"`
5. Check it sees your files: `rclone lsf gdrive:`
6. Switch it on for the night run:
   `mkdir -p ~/.config/pricepulse && echo 'PP_DRIVE_REMOTE=gdrive:' >> ~/.config/pricepulse/env && chmod 600 ~/.config/pricepulse/env ~/.config/rclone/rclone.conf`
7. Bring the code up to date and try it now: `cd ~/pricepulse && git pull --ff-only origin main && pp essentials`

It prints one line per file, such as `essentials.csv: 120 added, 3 already there, 2 skipped`.
Skipped rows are named with the reason. New items get prices after the next night run.

## New-item searches (paid and tester accounts)
A paid shopper who searches for something we do not list can tap "Search the stores for ...". The
server puts the word in a queue (after checking it fits Groceries, Fresh, Liquor, Health & Beauty or
pet food, adults only for liquor, 5 searches a day each). The desktop looks at the queue every 5
minutes, searches Checkers, Pick n Pay, Woolworths and Dis-Chem for that one word, uploads what the
stores' own pages show, and the item appears marked **New** for 14 days. Nothing is ever guessed.

Set up once (the same upload token as the night run, nothing else):
```
cd ~/pricepulse
git pull
scripts/linux/install_timer.sh        # now also installs pricepulse-ondemand.timer
systemctl --user list-timers --no-pager
```
Look at what it did: `cat ~/pricepulse-logs/ondemand-$(date +%Y%m).log` (nothing is written when no
search is waiting). Run one by hand: `scripts/linux/ondemand.sh`. The night run waits for a running
search to finish (up to 40 minutes) and no search starts while the night run is going, so the
searches simply wait until it is over. Search data is kept in `~/pricepulse-ondemand/` and removed
after each search.
