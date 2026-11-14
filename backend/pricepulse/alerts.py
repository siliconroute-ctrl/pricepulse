"""Email the owner when a store's scraper fails, so a broken store is not missed for days.

Settings come from environment variables only, never from a file in the repo (the mailbox
password must never be written to git or chat):

    PP_ALERT_SMTP_HOST   e.g. mail.trolleyup.com        (empty = alerts off)
    PP_ALERT_SMTP_PORT   587 (STARTTLS, default) or 465 (SSL)
    PP_ALERT_SMTP_USER   the mailbox login
    PP_ALERT_SMTP_PASSWORD
    PP_ALERT_FROM        sender address (default: the login)
    PP_ALERT_TO          who gets the alert (comma separated)
    PP_ALERT_REPEAT_HOURS  do not repeat the same store's alert within this time (default 24)

Test it:  python -m pricepulse.alerts --test
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
import sys
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from .scrapers.base import StoreHealth


def _cfg() -> dict[str, Any]:
    env = os.environ
    user = env.get("PP_ALERT_SMTP_USER", "")
    return {
        "host": env.get("PP_ALERT_SMTP_HOST", "").strip(),
        "port": int(env.get("PP_ALERT_SMTP_PORT", "587") or 587),
        "user": user,
        "password": env.get("PP_ALERT_SMTP_PASSWORD", ""),
        "sender": env.get("PP_ALERT_FROM", "").strip() or user,
        "to": [a.strip() for a in env.get("PP_ALERT_TO", "").split(",") if a.strip()],
        "repeat_hours": float(env.get("PP_ALERT_REPEAT_HOURS", "24") or 24),
    }


def configured() -> bool:
    c = _cfg()
    return bool(c["host"] and c["to"] and c["sender"])


def problems(healths: list[StoreHealth]) -> list[StoreHealth]:
    """Stores that need a person: blocked, failed (nothing found), or with errors and no prices."""
    return [
        h for h in healths if h.status in ("blocked", "failed") or (h.items == 0 and h.status != "skipped")
    ]


def _advice(h: StoreHealth) -> str:
    if h.status == "blocked":
        return "The website blocked us. Do not try to get round it; tell Claude."
    return (
        f'Run: run_scrapers.py --store "{h.store}" --limit 3 --debug --no-db --save-fixtures '
        "and send Claude the ONE file it names at the end."
    )


def build_message(bad: list[StoreHealth], when: str) -> tuple[str, str]:
    names = ", ".join(h.store for h in bad)
    subject = f"PricePulse: {names} needs attention"
    lines = [f"PricePulse price check, {when}.", ""]
    for h in bad:
        lines += [h.summary(), "  " + _advice(h), ""]
    lines += [
        "Old prices stay in the app with their date; nothing was invented.",
        "You get one email per store per day until it is fixed.",
    ]
    return subject, "\n".join(lines)


def send_email(subject: str, body: str) -> bool:
    """Send one email. Returns False (and says why) instead of raising."""
    c = _cfg()
    if not (c["host"] and c["to"] and c["sender"]):
        print("  Email alerts are not set up (PP_ALERT_SMTP_HOST and PP_ALERT_TO are empty).")
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, c["sender"], ", ".join(c["to"])
    msg.set_content(body)
    try:
        ctx = ssl.create_default_context()
        if c["port"] == 465:
            with smtplib.SMTP_SSL(c["host"], c["port"], context=ctx, timeout=30) as smtp:
                _login_send(smtp, c, msg)
        else:
            with smtplib.SMTP(c["host"], c["port"], timeout=30) as smtp:
                smtp.starttls(context=ctx)
                _login_send(smtp, c, msg)
    except Exception as exc:  # noqa: BLE001 - an alert problem must never stop a scrape
        print(f"  Could not send the alert email: {type(exc).__name__}: {exc}")
        return False
    return True


def _login_send(smtp: smtplib.SMTP, c: dict[str, Any], msg: EmailMessage) -> None:
    if c["user"]:
        smtp.login(c["user"], c["password"])
    smtp.send_message(msg)


def alert_if_needed(
    healths: list[StoreHealth], state_file: Path, when: str, now: datetime | None = None
) -> bool:
    """Email about stores that failed, at most once per store per PP_ALERT_REPEAT_HOURS."""
    now = now or datetime.now(UTC)
    bad = problems(healths)
    try:
        state = json.loads(state_file.read_text("utf-8"))
    except (OSError, ValueError):
        state = {}
    names = {h.store for h in bad}
    for gone in [s for s in state if s not in names]:
        del state[gone]  # a store that works again is forgotten, so a new failure alerts at once
    wait = timedelta(hours=_cfg()["repeat_hours"])
    fresh = []
    for h in bad:
        try:
            last = datetime.fromisoformat(state[h.store])
        except (KeyError, ValueError):
            last = None
        if last is None or now - last >= wait:
            fresh.append(h)
    sent = False
    if fresh and configured():
        subject, body = build_message(fresh, when)
        sent = send_email(subject, body)
        if sent:
            for h in fresh:
                state[h.store] = now.isoformat()
            print(f"  Alert emailed to {', '.join(_cfg()['to'])}: {', '.join(h.store for h in fresh)}.")
    elif fresh:
        print("  A store needs attention but email alerts are not set up (see pricepulse/alerts.py).")
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state), "utf-8")
    except OSError:
        pass
    return sent


def main() -> int:
    if "--test" not in sys.argv:
        print("Use: python -m pricepulse.alerts --test")
        return 2
    ok = send_email(
        "PricePulse: test alert", "This is a test. If you can read it, scraper alerts will reach you."
    )
    print("Test email sent." if ok else "Test email NOT sent.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
