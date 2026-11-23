"""Look for the items paid shoppers asked for, on the scraper machine (the Linux desktop).

    python search_worker.py              # take waiting searches, search the stores, upload, report back

Started every few minutes by a systemd user timer (see docs/AUTOMATION.md). For each waiting search
it runs the normal store scrapers for that one word, marks every row it finds as NEW, uploads the
prices over the same HTTPS token as the night run, and tells the server how many it found. It never
guesses a price: only what a store's own page shows is uploaded.

It keeps out of the night run's way: while the night run holds its lock this does nothing and the
searches simply wait. It uses its own data folder, so the night run's files are never touched.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from push_prices import DEFAULT_URL, read_token, send_file

BACKEND_DIR = Path(__file__).resolve().parent
WORK_DIR = Path(os.environ.get("PP_ONDEMAND_DIR", "~/pricepulse-ondemand")).expanduser()
NIGHT_LOCK = Path("~/pricepulse-logs/.nightly.lock").expanduser()
WORKER_LOCK = Path("~/pricepulse-logs/.ondemand.lock").expanduser()
MAX_JOBS = 3  # searches handled per run
SCRAPE_TIMEOUT = 20 * 60  # seconds for one search across all stores


def products_text(term: str, category: str) -> str:
    """A tiny products.txt holding the one word, so every store searches it under its own rules
    (Woolworths skips liquor, Dis-Chem only does health and pets, and so on)."""
    safe_term = " ".join(term.split())
    safe_cat = " ".join(category.split()) or "Groceries"
    return f"# category: {safe_cat}\n{safe_term}\n"


def mark_new(data: dict) -> int:
    """Mark every row of a price file as found by a shopper's search; return how many rows."""
    rows = data.get("items") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return 0
    for row in rows:
        if isinstance(row, dict):
            row["new"] = True
    return len(rows)


def api(method: str, url: str, token: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url.rstrip("/") + path,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "PricePulseZA-search-worker/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - fixed https address
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except (urllib.error.URLError, OSError, ValueError):
        return 0, {}


def run_scrapers(products_file: Path, data_dir: Path) -> int:
    env = dict(os.environ, PP_DATA_DIR=str(data_dir))
    try:
        done = subprocess.run(  # noqa: S603 - our own script
            [sys.executable, "run_scrapers.py", "--products", str(products_file), "--no-db", "--no-alert"],
            cwd=BACKEND_DIR,
            env=env,
            timeout=SCRAPE_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124
    return done.returncode


def process_job(job: dict, url: str, token: str, regions: list[str]) -> tuple[int, str]:
    """Search, mark and upload one job. Returns (rows found, error text; empty when all is well)."""
    data_dir = WORK_DIR / f"job{job['id']}"
    shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(products_text(job["term"], job.get("category", "Groceries")))
        products_file = Path(fh.name)
    try:
        code = run_scrapers(products_file, data_dir)
    finally:
        products_file.unlink(missing_ok=True)
    found = 0
    files: list[tuple[str, bytes]] = []
    for region in regions:
        path = data_dir / f"latest_{region}.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        n = mark_new(data)
        if n:
            found = max(found, n)
            files.append((region, json.dumps(data, ensure_ascii=False).encode("utf-8")))
    if not files:
        shutil.rmtree(data_dir, ignore_errors=True)
        if code not in (0, 1):
            return 0, "The stores could not be searched."
        return 0, ""
    for _region, body in files:
        status, _answer = send_file(url, token, body)
        if status != 200:
            return found, f"The server did not accept the prices ({status})."
    shutil.rmtree(data_dir, ignore_errors=True)
    return found, ""


def main() -> int:
    token = read_token()
    if not token:
        print("No upload token. Put it in ~/.config/pricepulse/upload_token (see docs/AUTOMATION.md).")
        return 1
    url = os.environ.get("PP_UPLOAD_URL", DEFAULT_URL)
    NIGHT_LOCK.parent.mkdir(parents=True, exist_ok=True)
    night = NIGHT_LOCK.open("a")
    try:
        fcntl.flock(night, fcntl.LOCK_EX | fcntl.LOCK_NB)  # free means the night run is not going
        fcntl.flock(night, fcntl.LOCK_UN)
    except OSError:
        print("The night run is going. New-item searches will wait.")
        return 0
    lock = WORKER_LOCK.open("a")  # the night run waits for this one before it starts
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("A search run is already going.")
        return 0
    status, answer = api("GET", url, token, f"/api/v1/ingest/searches?limit={MAX_JOBS}")
    if status != 200:
        print(f"Could not ask the server for searches (answer {status}).")
        return 0 if status == 0 else 1  # no connection is not worth an alarm
    jobs = answer.get("searches", [])
    if not jobs:
        return 0
    from pricepulse.regions import load_regions

    regions = list(load_regions(str(BACKEND_DIR / "regions.yaml")).regions)
    for job in jobs:
        print(f"=== search {job['id']}: {job['term']!r} ({job['category']}) ===")
        started = time.time()
        try:
            found, error = process_job(job, url, token, regions)
        except Exception as exc:  # noqa: BLE001 - one search must not stop the others
            found, error = 0, f"Search problem: {type(exc).__name__}"
        api("POST", url, token, f"/api/v1/ingest/searches/{job['id']}", {"found": found, "error": error})
        print(f"found {found}{' - ' + error if error else ''} in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
