"""Accounts, the age check, and lookup-session limits.

Rules (settings can change the numbers):
- A free session holds up to 45 looked-up items. Taking an item off the list does not
  give the slot back; clearing the session ends it.
- Everyone: 29 sessions started per South African calendar day and 45 items a session. The TrolleyUp
  plan (R39 a month) does not raise these limits: it lets a person send picked items to TrolleyUp.
  Only a tester (the owner, set by hand) has no limits. Automated lookups are not allowed on any plan.
- The ID check is needed before the first lookup. Under-18s do not see liquor.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import Settings
from .db import (
    AppUser,
    ItemRequest,
    LookupSession,
    Payment,
    Product,
    SearchJob,
    SessionItem,
    StoreOwner,
    Subscription,
    utcnow,
)
from .ingest import SAST
from .queries import visible_clause
from .said import ADULT_AGE, check_id, fingerprint

LIQUOR = "Liquor"


class RuleError(Exception):
    """A rule stopped the action. ``code`` is for the app; ``message`` is plain language."""

    def __init__(self, code: str, message: str, status: int = 403) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def sa_today(now: datetime | None = None) -> date:
    """Today's date in South Africa (limits reset at midnight SA time)."""
    now = now or utcnow()
    return (now + SAST.utcoffset(None)).date()


def get_or_create_user(s: Session, uid: str) -> AppUser:
    user = s.get(AppUser, uid)
    if user is None:
        user = AppUser(uid=uid)
        s.add(user)
        s.flush()
    return user


def is_adult(user: AppUser, today: date) -> bool:
    if user.adult:
        return True
    return bool(user.adult_on and user.adult_on <= today)


TESTER = "tester"  # set by hand with admin.py: unlimited sessions AND unlimited items
TESTER_ITEMS = 9999


def is_tester(user: AppUser) -> bool:
    return user.plan_override == TESTER


def items_cap(s: Session, user: AppUser, settings: Settings, now: datetime | None = None) -> int:
    """Items allowed in one session: 45 for everyone, no practical limit for a tester."""
    return TESTER_ITEMS if is_tester(user) else settings.session_items_max


def is_paid(s: Session, user: AppUser, now: datetime | None = None) -> bool:
    """Has the TrolleyUp plan (R39 a month): paid-up period not over (even after cancelling), or
    set by hand as paid or tester. The plan unlocks sending picked items to TrolleyUp only."""
    if user.plan_override in ("paid", TESTER):
        return True
    now = now or utcnow()
    sub = s.scalar(select(Subscription).where(Subscription.uid == user.uid))
    return bool(sub and sub.paid_until and sub.paid_until > now)


def verify_id(
    s: Session, user: AppUser, id_number: str, settings: Settings, today: date | None = None
) -> None:
    """Check the ID number, save the age result and fingerprint. The number is not kept."""
    today = today or sa_today()
    if not settings.id_secret or len(settings.id_secret) < 16:
        raise RuleError("not_set_up", "The ID check is not set up on the server yet. Please try later.", 503)
    info = check_id(id_number, today)
    if not info.valid:
        raise RuleError("bad_id", info.problem, 422)
    fp = fingerprint(id_number, settings.id_secret)
    if user.id_fingerprint and user.id_fingerprint != fp:
        raise RuleError("id_differs", "This account was already checked with a different ID number.", 409)
    other = s.scalar(select(AppUser).where(AppUser.id_fingerprint == fp, AppUser.uid != user.uid))
    if other is not None:
        raise RuleError(
            "id_in_use",
            "This ID number is already linked to another PricePulse account. "
            "Sign in with that account, or email us if you think this is a mistake.",
            409,
        )
    user.id_fingerprint = fp
    user.id_checked_at = utcnow()
    user.adult = info.age_on(today) >= ADULT_AGE
    user.adult_on = None if user.adult else info.adult_on()
    user.terms_version = settings.terms_version
    try:
        s.flush()
    except IntegrityError:
        s.rollback()
        raise RuleError("id_in_use", "This ID number is already linked to another account.", 409) from None


def current_session(s: Session, uid: str) -> LookupSession | None:
    return s.scalar(
        select(LookupSession)
        .where(LookupSession.uid == uid, LookupSession.cleared_at.is_(None))
        .order_by(LookupSession.id.desc())
    )


def sessions_today(s: Session, uid: str, today: date) -> int:
    return (
        s.scalar(
            select(func.count())
            .select_from(LookupSession)
            .where(LookupSession.uid == uid, LookupSession.day == today.isoformat())
        )
        or 0
    )


def add_item(
    s: Session, user: AppUser, product_id: int, settings: Settings, now: datetime | None = None
) -> LookupSession:
    """Look up one product: add it to the current session, starting one if needed."""
    now = now or utcnow()
    today = sa_today(now)
    if not user.id_fingerprint:
        raise RuleError("id_required", "Please confirm your age with your ID number first.")
    product = s.get(Product, product_id)
    if product is None:
        raise RuleError("not_found", "That product was not found.", 404)
    if product.category == LIQUOR and not is_adult(user, today):
        raise RuleError("adult_only", "Liquor prices are only for people aged 18 and over.")
    sess = current_session(s, user.uid)
    already = sess is not None and any(i.product_id == product_id for i in sess.items)
    if not already and s.scalar(select(Product.id).where(Product.id == product_id, visible_clause())) is None:
        raise RuleError(
            "not_found", "That product was not found.", 404
        )  # hidden by the admin or an extra hit
    if sess is not None:
        existing = next((i for i in sess.items if i.product_id == product_id), None)
        if existing is not None:
            existing.on_list = True
            return sess
        cap = items_cap(s, user, settings, now)
        if len(sess.items) >= cap:
            raise RuleError(
                "session_full",
                f"This session is full ({cap} items). " "Clear the session to start a new one.",
                409,
            )
    else:
        used = sessions_today(s, user.uid, today)
        if not is_tester(user) and used >= settings.free_sessions_per_day:
            raise RuleError(
                "daily_limit",
                f"You have used your {settings.free_sessions_per_day} lookup sessions for today. "
                "Please come back tomorrow.",
            )
        sess = LookupSession(uid=user.uid, day=today.isoformat(), started_at=now)
        s.add(sess)
        s.flush()
    sess.items.append(SessionItem(product_id=product_id, quantity=1, on_list=True, added_at=now))
    s.flush()
    return sess


def update_item(
    s: Session, user: AppUser, product_id: int, quantity: int | None, on_list: bool | None
) -> LookupSession:
    sess = current_session(s, user.uid)
    item = next((i for i in sess.items if i.product_id == product_id), None) if sess else None
    if item is None:
        raise RuleError("not_in_session", "That item is not in your current session.", 404)
    if quantity is not None:
        item.quantity = max(1, min(99, quantity))
    if on_list is not None:
        item.on_list = on_list
    s.flush()
    return sess


def clear_session(s: Session, user: AppUser, now: datetime | None = None) -> None:
    sess = current_session(s, user.uid)
    if sess is not None:
        sess.cleared_at = now or utcnow()
        s.flush()


def delete_user(s: Session, user: AppUser) -> None:
    """Remove everything we hold about this person, except payment records needed for tax."""
    for sess in s.scalars(select(LookupSession).where(LookupSession.uid == user.uid)).all():
        s.delete(sess)
    sub = s.scalar(select(Subscription).where(Subscription.uid == user.uid))
    if sub is not None:
        s.delete(sub)
    for req in s.scalars(select(ItemRequest).where(ItemRequest.uid == user.uid)).all():
        s.delete(req)
    for job in s.scalars(select(SearchJob).where(SearchJob.uid == user.uid)).all():
        s.delete(job)
    link = s.get(StoreOwner, user.uid)
    if link is not None:  # the store stays; only this person's link to it goes
        s.delete(link)
    for pay in s.scalars(select(Payment).where(Payment.uid == user.uid)).all():
        pay.uid = "deleted"
    s.flush()
    s.delete(user)
    s.flush()


MAX_OPEN_REQUESTS = 10


def clean_request(text: str) -> str:
    """One product wish: letters, digits and a few marks, 2 to 60 characters, no web addresses."""
    import re

    term = re.sub(r"\s+", " ", (text or "").strip())
    if len(term) < 2 or len(term) > 60:
        raise RuleError("bad_request", "Please type the item in 2 to 60 characters.", 422)
    if re.search(r"https?:|www\.|@|\.(com|co\.za|net|org)\b", term, re.I) or not re.fullmatch(
        r"[\w .,'&/%+()-]+", term
    ):
        raise RuleError(
            "bad_request", "Please type only the item name, for example: white mushrooms 250g.", 422
        )
    return term


def add_request(s: Session, user: AppUser, text: str) -> ItemRequest:
    """Save a request. Asking for the same thing twice is fine and counts once."""
    term = clean_request(text)
    key = term.lower()
    mine = s.scalars(
        select(ItemRequest).where(ItemRequest.uid == user.uid, ItemRequest.status == "open")
    ).all()
    for r in mine:
        if r.key == key:
            return r
    if len(mine) >= MAX_OPEN_REQUESTS:
        raise RuleError(
            "too_many_requests",
            f"You already have {MAX_OPEN_REQUESTS} requests waiting. We will add them soon.",
            429,
        )
    req = ItemRequest(uid=user.uid, term=term, key=key)
    s.add(req)
    s.flush()
    return req
