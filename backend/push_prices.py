"""Send the checked price files to the server over HTTPS (the hands-off upload).

    python push_prices.py                 # every town in regions.yaml
    python push_prices.py --max-hours 36

Needs the upload token (the same long random value as PP_INGEST_TOKEN on the server). It is read
from the environment variable PP_UPLOAD_TOKEN, or from the first line of
~/.config/pricepulse/upload_token (keep that file readable by you only). The server address is
PP_UPLOAD_URL (default https://pricepulse.trolleyup.com). The token is never printed or logged.

Exit code 0 = every town was loaded; 1 = something was wrong (the reason is printed, and an
alert email is sent when email alerts are set up).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from pricepulse.config import get_settings
from pricepulse.regions import load_regions
from pricepulse.scrapers.stores import ACTIVE_SCRAPERS
from upload_check import check_file

DEFAULT_URL = "https://pricepulse.trolleyup.com"
TOKEN_FILE = Path("~/.config/pricepulse/upload_token")


def read_token(environ: dict[str, str] | None = None, token_file: Path | None = None) -> str:
    environ = os.environ if environ is None else environ
    token = environ.get("PP_UPLOAD_TOKEN", "").strip()
    if token:
        return token
    path = (token_file or TOKEN_FILE).expanduser()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    return lines[0].strip() if lines else ""


def send_file(
    url: str, token: str, body: bytes, timeout: float = 120, tries: int = 3
) -> tuple[int, dict | str]:
    """POST one price file. Retries network trouble and server errors, never a refusal."""
    req = urllib.request.Request(
        url.rstrip("/") + "/api/v1/ingest/prices",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "PricePulseZA-uploader/1.0",
        },
    )
    last: tuple[int, dict | str] = (0, "no answer")
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            last = (exc.code, detail)
            if exc.code < 500:
                return last
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = (0, f"could not reach the server ({exc})")
        if attempt < tries - 1:
            time.sleep(5 * (attempt + 1))
    return last


def alert(subject: str, body: str) -> None:
    try:
        from pricepulse.alerts import send_email

        send_email(subject, body)
    except Exception as exc:  # noqa: BLE001 - never let an alert problem hide the real one
        print(f"  (Could not send the alert email: {exc})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Upload price files to the server")
    ap.add_argument("--max-hours", type=float, default=36.0)
    ap.add_argument("--url", default=os.environ.get("PP_UPLOAD_URL", DEFAULT_URL))
    args = ap.parse_args(argv)
    token = read_token()
    if not token:
        print("No upload token. Put it in ~/.config/pricepulse/upload_token (see docs/AUTOMATION.md).")
        return 1
    settings = get_settings()
    regions = load_regions(str(settings.regions_file))
    expected = [c.name for c in ACTIVE_SCRAPERS]
    jobs: list[tuple[str, Path]] = []
    bad = 0
    for slug in regions.regions:
        path = settings.data_dir / f"latest_{slug}.json"
        problems, warnings, per_store = check_file(path, args.max_hours, expected)
        for text in warnings:
            print(f"  Note: {text}")
        for text in problems:
            print(f"  PROBLEM: {text}")
        if problems:
            bad += 1
        else:
            jobs.append((slug, path))
    if bad:
        msg = (
            f"Nothing was uploaded: {bad} of {len(regions.regions)} town files have a problem (see the log)."
        )
        print(msg)
        alert("PricePulse: price upload stopped", msg)
        return 1
    failed: list[str] = []
    for slug, path in jobs:
        status, answer = send_file(args.url, token, path.read_bytes())
        if status == 200 and isinstance(answer, dict):
            town = regions.regions[slug].name
            print(f"  {town}: {answer['saved']} prices loaded, {answer['by_barcode']} joined by barcode.")
        else:
            print(f"  {regions.regions[slug].name}: NOT loaded (answer {status}: {answer})")
            failed.append(slug)
    if failed:
        msg = "The server did not load: " + ", ".join(failed) + ". See the log for the answers."
        print(msg)
        alert("PricePulse: price upload failed", msg)
        return 1
    print("All towns loaded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
