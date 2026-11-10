"""PayFast monthly subscription (R99 a month).

STATUS: written from PayFast's published integration rules, tested here only with
signatures we generate ourselves. It has NOT been run against PayFast's sandbox
(the build machine could not reach it). Test in sandbox before taking real money:
see docs/PAYMENTS.md.

Flow:
1. The app asks POST /billing/checkout. We return PayFast's address and signed form
   fields; the phone posts them to PayFast, where the person pays.
2. PayFast calls POST /billing/payfast/itn (server to server) for the first and
   every monthly payment, and when a subscription is cancelled. We check the
   signature, merchant, amount and ask PayFast to confirm, then update the plan.
"""

from __future__ import annotations

import calendar
import hashlib
import logging
import secrets
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .db import AppUser, Payment, Subscription, utcnow

log = logging.getLogger("pricepulse.billing")
GRACE_DAYS = 3  # card retries: keep access a few days past the due date


def host(settings: Settings) -> str:
    return "sandbox.payfast.co.za" if settings.payfast_sandbox else "www.payfast.co.za"


def configured(settings: Settings) -> bool:
    return bool(settings.payfast_merchant_id and settings.payfast_merchant_key)


def _enc(value: str) -> str:
    return urllib.parse.quote_plus(str(value).strip())


def param_string(pairs: list[tuple[str, str]], skip_empty: bool) -> str:
    return "&".join(f"{k}={_enc(v)}" for k, v in pairs if k != "signature" and (v != "" or not skip_empty))


def sign(pairs: list[tuple[str, str]], passphrase: str, skip_empty: bool = True) -> str:
    """PayFast signature: MD5 of the fields in order, plus the passphrase."""
    text = param_string(pairs, skip_empty)
    if passphrase:
        text += "&passphrase=" + _enc(passphrase)
    return hashlib.md5(text.encode()).hexdigest()  # noqa: S324 - PayFast's protocol, not our choice


def rand(cents: int) -> str:
    return f"{cents / 100:.2f}"


def checkout(s: Session, user: AppUser, email: str, settings: Settings) -> dict:
    """Signed form fields for starting the subscription. Field order matters to PayFast."""
    sub = s.scalar(select(Subscription).where(Subscription.uid == user.uid))
    if sub is None:
        sub = Subscription(uid=user.uid)
        s.add(sub)
    sub.m_payment_id = "pp-" + secrets.token_hex(12)
    if sub.status not in ("active", "cancelled"):
        sub.status = "pending"  # a cancelled plan keeps its paid-up time until the new payment lands
    s.flush()
    amount = rand(settings.price_cents)
    pairs = [
        ("merchant_id", settings.payfast_merchant_id),
        ("merchant_key", settings.payfast_merchant_key),
        ("return_url", settings.public_url + "/#account/paid"),
        ("cancel_url", settings.public_url + "/#account/cancelled"),
        ("notify_url", settings.public_url + "/api/v1/billing/payfast/itn"),
        ("email_address", email),
        ("m_payment_id", sub.m_payment_id),
        ("amount", amount),
        ("item_name", "PricePulse ZA monthly plan"),
        ("item_description", "Unlimited price-lookup sessions, billed monthly"),
        ("subscription_type", "1"),
        ("recurring_amount", amount),
        ("frequency", "3"),  # monthly
        ("cycles", "0"),  # until cancelled
    ]
    pairs = [(k, v) for k, v in pairs if v != ""]
    pairs.append(("signature", sign(pairs, settings.payfast_passphrase)))
    return {"action_url": f"https://{host(settings)}/eng/process", "fields": dict(pairs)}


def _remote_valid(settings: Settings, body: str) -> bool:
    """Ask PayFast whether this notice really came from them."""
    req = urllib.request.Request(  # noqa: S310 - fixed https host
        f"https://{host(settings)}/eng/query/validate",
        data=body.encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        return resp.read().decode().strip().upper().startswith("VALID")


def add_month(when: datetime) -> datetime:
    y, m = (when.year + 1, 1) if when.month == 12 else (when.year, when.month + 1)
    return when.replace(year=y, month=m, day=min(when.day, calendar.monthrange(y, m)[1]))


def handle_itn(
    s: Session,
    pairs: list[tuple[str, str]],
    settings: Settings,
    remote_valid: Callable[[Settings, str], bool] = _remote_valid,
    now: datetime | None = None,
) -> str:
    """Process one PayFast notice. Returns a short outcome word (also logged)."""
    now = now or utcnow()
    data = dict(pairs)
    if not configured(settings):
        return "not_configured"
    if sign(pairs, settings.payfast_passphrase, skip_empty=False) != data.get("signature", ""):
        return "bad_signature"
    if data.get("merchant_id") != settings.payfast_merchant_id:
        return "wrong_merchant"
    sub = s.scalar(select(Subscription).where(Subscription.m_payment_id == data.get("m_payment_id", "")))
    if sub is None and data.get("token"):
        sub = s.scalar(select(Subscription).where(Subscription.token == data["token"]))
    if sub is None:
        return "unknown_subscription"
    if settings.payfast_validate:
        try:
            if not remote_valid(settings, param_string(pairs, skip_empty=False)):
                return "not_confirmed"
        except Exception as exc:  # noqa: BLE001
            log.error("Could not confirm a payment notice with PayFast: %s", exc)
            return "confirm_failed"
    status = data.get("payment_status", "").upper()
    if data.get("token"):
        sub.token = data["token"]
    if status == "CANCELLED":
        sub.status = "cancelled"
        sub.cancelled_at = now
        return "cancelled"
    if status != "COMPLETE":
        return "ignored"
    cents = int(round(float(data.get("amount_gross") or 0) * 100))
    if cents != settings.price_cents:
        return "wrong_amount"
    pay_id = data.get("pf_payment_id", "")
    if not pay_id or s.scalar(select(Payment).where(Payment.provider_payment_id == pay_id)):
        return "duplicate"
    s.add(
        Payment(
            uid=sub.uid, provider_payment_id=pay_id, amount_cents=cents, status="complete", received_at=now
        )
    )
    base = max(now, (sub.paid_until - timedelta(days=GRACE_DAYS)) if sub.paid_until else now)
    sub.paid_until = add_month(base) + timedelta(days=GRACE_DAYS)
    sub.status = "active"
    sub.cancelled_at = None
    sub.started_at = sub.started_at or now
    s.flush()
    return "paid"


def cancel_remote(settings: Settings, token: str, now: datetime | None = None) -> bool:
    """Ask PayFast to stop a subscription. UNVERIFIED against the live API."""
    now = now or utcnow()
    headers = {
        "merchant-id": settings.payfast_merchant_id,
        "version": "v1",
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    pairs = sorted(headers.items())
    if settings.payfast_passphrase:
        pairs = sorted(pairs + [("passphrase", settings.payfast_passphrase)])
    headers["signature"] = hashlib.md5(  # noqa: S324
        "&".join(f"{k}={_enc(v)}" for k, v in pairs).encode()
    ).hexdigest()
    url = f"https://api.payfast.co.za/subscriptions/{urllib.parse.quote(token)}/cancel"
    if settings.payfast_sandbox:
        url += "?testing=true"
    req = urllib.request.Request(url, method="PUT", headers=headers, data=b"")  # noqa: S310
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        return 200 <= resp.status < 300
