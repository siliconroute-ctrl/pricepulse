"""Opening hours for a local store: checked when the owner types them, shown as Open now / Closed.

Hours are stored as JSON, ``{"mon": ["08:00", "17:00"], "tue": ["08:00", "17:00"], "sun": null}``
(a day with ``null`` or no entry is closed). South African time is UTC+2 all year (no daylight saving).
A shop that opens and closes on the same day is all this supports (no overnight hours).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
SA_OFFSET = timedelta(hours=2)
SOON = 60  # minutes before closing when "Closing soon" is shown
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _minutes(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def parse_hours(days: dict | None) -> dict[str, list[str] | None]:
    """Check what the owner typed. Raises ValueError with a plain sentence for the first problem."""
    out: dict[str, list[str] | None] = {}
    for key, val in (days or {}).items():
        if key not in DAYS:
            raise ValueError("Unknown day in the opening hours.")
        name = NAMES[DAYS.index(key)]
        if val in (None, [], ""):
            out[key] = None
            continue
        if not (isinstance(val, (list, tuple)) and len(val) == 2):
            raise ValueError(f"{name}: type the opening and closing time, for example 08:00 and 17:00.")
        a, b = str(val[0]).strip(), str(val[1]).strip()
        if not _TIME.match(a) or not _TIME.match(b):
            raise ValueError(f"{name}: times must look like 08:00 or 17:30 (24-hour clock).")
        if _minutes(b) <= _minutes(a):
            raise ValueError(f"{name}: the closing time must be later than the opening time.")
        out[key] = [a, b]
    return out


def dump_hours(days: dict[str, list[str] | None]) -> str:
    return json.dumps({k: days.get(k) for k in DAYS}, separators=(",", ":")) if any(days.values()) else ""


def load_hours(text: str) -> dict[str, list[str] | None]:
    try:
        data = json.loads(text) if text else {}
        return parse_hours(data) if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _span(slot: list[str] | None) -> str:
    return f"{slot[0]} to {slot[1]}" if slot else "Closed"


def open_status(text: str, note: str = "", now_utc: datetime | None = None) -> dict:
    """What a shopper reads: state (open, closing, closed, unknown), a sentence and the week."""
    hours = load_hours(text)
    week = [[NAMES[i], _span(hours.get(k))] for i, k in enumerate(DAYS)]
    base = {"week": week, "note": note or ""}
    if not any(hours.values()):
        return {**base, "state": "unknown", "text": "Opening hours not given", "today": ""}
    now = (now_utc or datetime.now(UTC).replace(tzinfo=None)) + SA_OFFSET
    idx = now.weekday()
    today = hours.get(DAYS[idx])
    mins = now.hour * 60 + now.minute
    today_text = _span(today)
    if today and _minutes(today[0]) <= mins < _minutes(today[1]):
        left = _minutes(today[1]) - mins
        state = "closing" if left <= SOON else "open"
        word = "Closing soon" if state == "closing" else "Open now"
        return {**base, "state": state, "text": f"{word} · closes at {today[1]}", "today": today_text}
    if today and mins < _minutes(today[0]):
        return {**base, "state": "closed", "text": f"Closed · opens today at {today[0]}", "today": today_text}
    for step in range(1, 8):
        j = (idx + step) % 7
        slot = hours.get(DAYS[j])
        if slot:
            when = "tomorrow" if step == 1 else NAMES[j]
            return {
                **base,
                "state": "closed",
                "text": f"Closed · opens {when} at {slot[0]}",
                "today": today_text,
            }
    return {**base, "state": "closed", "text": "Closed", "today": today_text}
