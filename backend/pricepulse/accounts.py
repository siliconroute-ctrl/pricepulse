"""Accounts, the age check, and lookup-session limits.

Rules (settings can change the numbers):
- A session holds up to 45 looked-up items. Taking an item off the list does not
  give the slot back; clearing the session ends it.
- Free plan: 3 sessions started per South African calendar day. Paid plan
  (R99 a month): no daily limit; still 45 items per session.
- The ID check is needed before the first lookup. Under-18s do not see liquor.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import Settings
from .db import AppUser, LookupSession, Payment, Product, SessionItem, Subscription, utcnow
from .ingest import SAST
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


def is_paid(s: Session, user: AppUser, now: datetime | None = None) -> bool:
    """Paid while the paid-up period lasts, even after cancelling."""
    if user.plan_override == "paid":
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
    if sess is not None:
        existing = next((i for i in sess.items if i.product_id == product_id), None)
        if existing is not None:
            existing.on_list = True
            return sess
        if len(sess.items) >= settings.session_items_max:
            raise RuleError(
                "session_full",
                f"This session is full ({settings.session_items_max} items). "
                "Clear the session to start a new one.",
                409,
            )
    else:
        used = sessions_today(s, user.uid, today)
        if not is_paid(s, user, now) and used >= settings.free_sessions_per_day:
            raise RuleError(
                "daily_limit",
                f"You have used your {settings.free_sessions_per_day} free sessions for today. "
                "Come back tomorrow, or upgrade for unlimited sessions.",
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
    for pay in s.scalars(select(Payment).where(Payment.uid == user.uid)).all():
        pay.uid = "deleted"
    s.flush()
    s.delete(user)
    s.flush()
