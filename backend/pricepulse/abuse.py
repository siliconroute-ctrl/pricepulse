"""No bots, no scraping: pause an account that looks automated (every plan, paid included).

A person looking things up on a phone is slow and irregular. These checks only trip on speed or on a
perfectly regular rhythm. The pause is temporary (30 minutes; 24 hours for a second pause within a day),
the account can still sign in and see its list, and the admin can lift it. Testers and admins are exempt
from the automatic pause (the Terms still apply to them).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .accounts import RuleError, is_tester
from .config import get_settings
from .db import AbuseLock, AppUser, utcnow

WINDOW_S = 60
FIRST_LOCK = timedelta(minutes=30)
REPEAT_LOCK = timedelta(hours=24)
RHYTHM_JITTER = 0.05  # calls in a row with gaps within 5% of each other are a machine

_hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def reset() -> None:
    """For tests."""
    _hits.clear()


def _locked_row(s: Session, uid: str) -> AbuseLock | None:
    return s.scalar(
        select(AbuseLock)
        .where(AbuseLock.uid == uid, AbuseLock.released.is_(False), AbuseLock.locked_until > utcnow())
        .order_by(AbuseLock.id.desc())
    )


def locked_message(row: AbuseLock) -> str:
    return (
        "Your account is paused because it looked automated. PricePulse is for people, not scripts "
        f"(see the Terms). It unlocks at {(row.locked_until + timedelta(hours=2)).strftime('%H:%M')} "
        "(South African time)."
    )


def _is_machine(q: deque[float], calls: int) -> bool:
    if calls <= 0 or len(q) < calls:
        return False
    stamps = list(q)[-calls:]
    gaps = [b - a for a, b in zip(stamps, stamps[1:], strict=False)]
    mean = sum(gaps) / len(gaps)
    return mean > 0.2 and all(abs(g - mean) <= mean * RHYTHM_JITTER for g in gaps)


def _lock(s: Session, user: AppUser, reason: str) -> AbuseLock:
    day_ago = utcnow() - timedelta(hours=24)
    earlier = s.scalar(
        select(func.count())
        .select_from(AbuseLock)
        .where(AbuseLock.uid == user.uid, AbuseLock.locked_at > day_ago)
    )
    row = AbuseLock(
        uid=user.uid, reason=reason, locked_until=utcnow() + (REPEAT_LOCK if earlier else FIRST_LOCK)
    )
    s.add(row)
    s.commit()
    try:  # tell the owner; an email problem must never block the answer
        from .alerts import send_email

        send_email("PricePulse: an account was paused", f"{user.uid} was paused: {reason}.")
    except Exception:  # noqa: BLE001
        pass
    return row


def guard(s: Session, user: AppUser, kind: str, now: float | None = None) -> None:
    """Call at the top of lookups, searches and exports. Raises 429 ``locked`` for a paused account and
    pauses an account whose pace is not human."""
    row = _locked_row(s, user.uid)
    if row is not None:
        raise RuleError("locked", locked_message(row), 429)
    if is_tester(user) or user.is_admin:
        return
    t = time.monotonic() if now is None else now
    q = _hits[(user.uid, kind)]
    q.append(t)
    while q and t - q[0] > WINDOW_S * 5:
        q.popleft()
    recent = sum(1 for x in q if t - x <= WINDOW_S)
    cfg = get_settings()
    limit = cfg.abuse_lookups_per_min if kind == "lookup" else cfg.abuse_searches_per_min
    if recent > limit:
        raise RuleError("locked", locked_message(_lock(s, user, f"{recent} {kind} calls in a minute")), 429)
    if _is_machine(q, cfg.abuse_rhythm_calls):
        raise RuleError("locked", locked_message(_lock(s, user, f"{kind} calls at a machine rhythm")), 429)


def release(s: Session, uid: str) -> int:
    n = 0
    for row in s.scalars(select(AbuseLock).where(AbuseLock.uid == uid, AbuseLock.released.is_(False))):
        row.released = True
        n += 1
    _hits.pop((uid, "lookup"), None)
    _hits.pop((uid, "search"), None)
    s.flush()
    return n
