# Deploying PricePulse ZA for testing

**Plan:** the app and its API run on a small Linux server, in Docker, behind Caddy
(which provides HTTPS). The price **scrapers run on the owner's Windows PC**, with a
South African internet address, because retailers may block or re-price a data-centre
address. Prices will be copied to the server from the PC (not built yet).

| Part | Where it runs |
|---|---|
| App (web page) and API | Server, one Docker container (`api`) |
| HTTPS and the address | Caddy on the server |
| Scrapers | The owner's PC, for now |
| Database | SQLite file in a Docker volume on the server |

**Test server:** Contabo Cloud VPS in Germany (Ubuntu 24.04, 6 vCPU, 12 GB, month by
month). Address: `pricepulse.trolleyup.com`. If the stores block scrapers run from it, or
South African speed matters, the same steps work on a South African provider
(xneelo Cloud, or a fixed-price VPS there).

> Status (11 Nov 2026): the server is **hardened and serving a test page**. The app is
> **not deployed on it yet** (steps 5 onward). Nothing here has been run against
> PayFast, and Firebase sign-in has not been tried on this address.

## DNS notes (this caused a long delay once)

- `trolleyup.com` DNS is at **xneelo**. The main site and email are on a different server
  (129.232.137.82). **Never change those records.**
- xneelo's panel had created a separate zone called `pricepulse.trolleyup.com`. Inside that
  zone, a record named `pricepulse` means `pricepulse.pricepulse.trolleyup.com`, which is
  wrong. The fix was to set the **`@` A record in that zone** to the server's IP (TTL 300).
- After any DNS change, ask xneelo's own server what it says:
  `nslookup pricepulse.trolleyup.com ns1.host-h.net`
  Your router, 8.8.8.8 and 1.1.1.1 may show the old answer for up to 2 hours.
- The API does not need its own name: it answers at `https://pricepulse.trolleyup.com/api/...`.

## 1 to 4: Server preparation (done on the Contabo test server)

Repeat these on any new server. Log in as `root` the first time only.

1. **Update:** `apt update && apt upgrade -y`. If a brand new server cannot look up names
   ("Temporary failure resolving"), create `/etc/systemd/resolved.conf.d/dns.conf`
   containing `[Resolve]`, `DNS=1.1.1.1 8.8.8.8`, `FallbackDNS=9.9.9.9`, then
   `systemctl restart systemd-resolved`.
2. **Firewall:** `ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable`
3. **HTTPS front door:** `apt install -y caddy`
4. **Safe logins.** On your PC (PowerShell) make a key (use a passphrase of four
   unrelated words) and show its public half:
   ```
   ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\id_ed25519
   Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub
   ```
   On the server, as root, put that line in `~/.ssh/authorized_keys` (the file needs
   `chmod 700 ~/.ssh` and `chmod 600 ~/.ssh/authorized_keys`), then create a normal user:
   ```
   adduser --gecos "" deploy
   usermod -aG sudo deploy
   mkdir -p /home/deploy/.ssh && cp ~/.ssh/authorized_keys /home/deploy/.ssh/
   chown -R deploy:deploy /home/deploy/.ssh
   chmod 700 /home/deploy/.ssh && chmod 600 /home/deploy/.ssh/authorized_keys
   ```
   **Test both logins from a second window** (`ssh -i ... deploy@IP` then `sudo whoami`).
   Only then turn off password login. The `00-` name matters: some servers have a setup
   file that switches passwords back on, and the first value read wins.
   ```
   cat > /etc/ssh/sshd_config.d/00-hardening.conf <<'EOF'
   PasswordAuthentication no
   KbdInteractiveAuthentication no
   PermitRootLogin prohibit-password
   PubkeyAuthentication yes
   EOF
   sshd -t && sshd -T | grep -q '^passwordauthentication no$' && systemctl restart ssh
   ```
   Check from outside: `ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password root@IP`
   must say `Permission denied (publickey).`
5. **Automatic protection**, as `deploy`:
   ```
   sudo apt install -y fail2ban unattended-upgrades
   printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' | sudo tee /etc/apt/apt.conf.d/20auto-upgrades
   sudo systemctl enable --now fail2ban
   ```
   Security updates now install themselves but the server does **not** reboot by itself,
   so every month or two: `ls /var/run/reboot-required`, and if it exists, `sudo reboot`.
6. **Reboot once**, then check `uname -r`, `systemctl is-active caddy fail2ban ssh`,
   `sudo ufw status`, and that the page still loads.
7. **Snapshot** in the Contabo panel (VPS, your server, Snapshots), named for the date.
   Rolling back to a snapshot also undoes everything done after it.
8. **Check port 80 from outside** (Contabo's own panel firewall can block it): on your PC,
   `curl.exe -I http://pricepulse.trolleyup.com` should answer
   `308 Permanent Redirect` to `https://...`.

## 5. Put the app on the server

Everything from here is run as `deploy`. Docker commands use `sudo` on purpose: being in
the `docker` group is the same as being root.

**5a. Docker and git**
```
curl -fsSL https://get.docker.com | sudo sh
sudo docker --version
sudo apt install -y git
```

**5b. The code.** The repo is private, so the server gets its own read-only key:
```
ssh-keygen -t ed25519 -f ~/.ssh/pricepulse_deploy -N ""
cat ~/.ssh/pricepulse_deploy.pub
```
In GitHub: the **pricepulse** repo, **Settings**, **Deploy keys**, **Add deploy key**,
title `server`, paste the line, leave **Allow write access** unticked. Then:
```
printf 'Host github.com\n  IdentityFile ~/.ssh/pricepulse_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
chmod 600 ~/.ssh/config
git clone git@github.com:siliconroute-ctrl/pricepulse.git
cd pricepulse
```
(Answer `yes` when asked to trust github.com.)

**5c. Settings**
```
cp backend/.env.example backend/.env
python3 -c "import secrets; print(secrets.token_hex(32))"
nano backend/.env
```
Set these four lines (use the long secret just printed for `PP_ID_SECRET`):
```
PP_PUBLIC_URL=https://pricepulse.trolleyup.com
PP_ALLOWED_ORIGINS=https://pricepulse.trolleyup.com
PP_TRUST_PROXY=1
PP_ID_SECRET=<the long secret>
```
Leave the PayFast lines empty and `PP_AUTH_DEV` and `PP_OPEN_PRICES` at 0. The server refuses
to start otherwise. Keep a copy of `PP_ID_SECRET` somewhere safe off the server and never
change it: it is what keeps ID fingerprints safe. Never put it in GitHub or in chat.

**5d. Start the app (not the scraper)**
```
sudo docker compose up -d --build api
sudo docker compose logs api --tail 20
curl http://127.0.0.1:8000/api/v1/health
```
The health answer should start `{"status":"empty",...` (no prices yet). The app listens
on `127.0.0.1:8000` only, so it cannot be reached from the internet except through Caddy.

**5e. Point Caddy at it.** Replace the test page with the app:
```
sudo nano /etc/caddy/Caddyfile
```
so the whole file reads:
```
pricepulse.trolleyup.com {
    reverse_proxy 127.0.0.1:8000
}
```
Then `sudo systemctl reload caddy` and open `https://pricepulse.trolleyup.com`.

**5f. Allow sign-in on this address.** In the Firebase console, project **trolleyup**,
**Authentication**, **Settings**, **Authorized domains**, **Add domain**:
`pricepulse.trolleyup.com`. If the project's web API key is restricted by website (Google
Cloud console, APIs and Services, Credentials), add the address there too. Details and
what is untested: [ACCOUNTS.md](ACCOUNTS.md).

**5g. Example prices for testing the screens (optional).** Until real prices arrive:
```
sudo docker compose exec api python make_sample.py --regions paarl,wellington
```
The app shows an "Example prices" warning for these. They are made up. When real prices
are ready, remove only the examples (accounts and age checks stay):
```
sudo docker compose exec api python admin.py purge-sample          # shows what it would remove
sudo docker compose exec api python admin.py purge-sample --yes    # does it
```
**Until the legal review is done, let only yourself and people you know sign in.** The age
check collects ID numbers, so POPIA applies even during testing.

## Updating later
```
cd ~/pricepulse
git pull
sudo docker compose up -d --build api
```

## Backups
The database holds accounts, age results (not ID numbers), fingerprints and subscriptions.
Treat every copy as private.
```
cd ~/pricepulse
sudo docker compose exec api rm -f /data/backup.db
sudo docker compose exec api python -c "import sqlite3; s=sqlite3.connect('/data/pricepulse.db'); s.execute(\"VACUUM INTO '/data/backup.db'\")"
sudo docker compose cp api:/data/backup.db ./pricepulse-backup-$(date +%F).db
```
Copy it to your PC, then delete it from the server: from PowerShell,
`scp -i $env:USERPROFILE\.ssh\id_ed25519 deploy@SERVER_IP:pricepulse/pricepulse-backup-*.db .`
Contabo's Auto Backup (about EUR 3.35 a month) is worth switching on once real accounts
exist.

## Getting real prices onto the server
Run the scrapers on your PC (`python run_scrapers.py` writes `data/latest_{town}.json`), then one
command sends the files and loads them. Key login only, no password in any script.

**One-time setup**
1. On the server, bring the code up to date and rebuild (the matching rules changed), and make
   the folder the files land in:
   ```
   cd ~/pricepulse
   git pull
   sudo docker compose up -d --build api
   mkdir -p ~/pricepulse-upload
   ```
2. On your PC, tell it where the server is (not a secret; your own `deploy` user and IP):
   ```
   setx PP_SERVER "deploy@YOUR_SERVER_IP"
   ```
   Close PowerShell and open a new window.

**Every time**
```
cd C:\Users\joao\pricepulse
git pull
.\scripts\upload_prices.ps1
```
It checks the files first (all seven towns present, not older than 36 hours, not example data,
prices per store shown) and stops with a plain message if anything is wrong. It asks for `y`
before sending. On the server it loads the files, shows how many example prices exist, and
deletes the uploaded files. Add `-RemoveExamples` once you are happy with the real prices:
`.\scripts\upload_prices.ps1 -RemoveExamples` (removes only the made-up prices; accounts and
age checks are not touched).

## Optional: app on Firebase Hosting, API on the server

Do the server steps above first, but use a subdomain like `api.trolleyup.com`
for the API. Then on your own computer:

```bash
npm install -g firebase-tools
firebase login
cd pricepulse
```

Tell the app where the API is (this file is the only thing that changes):

**Linux/macOS**
```bash
echo 'window.PRICEPULSE_CONFIG = { apiBase: "https://api.trolleyup.com" };' > frontend/config.js
```
**Windows (PowerShell)**
```powershell
Set-Content frontend\config.js 'window.PRICEPULSE_CONFIG = { apiBase: "https://api.trolleyup.com" };'
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
3. URL: `https://pricepulse.trolleyup.com/api/v1/health?strict=1`
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
