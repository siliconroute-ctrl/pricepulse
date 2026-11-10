"""PayFast subscription: signed checkout fields and payment notices (ITN).

These prove our own logic. They do not prove PayFast accepts our signature: that
needs a sandbox run (docs/PAYMENTS.md).
"""

from datetime import datetime, timedelta
from urllib.parse import urlencode

import pytest
from conftest import make_id
from fastapi.testclient import TestClient
from sqlalchemy import select

from pricepulse import payfast
from pricepulse.api.app import create_app
from pricepulse.db import Payment, Subscription, session_factory, utcnow

AUTH = {"Authorization": "Bearer dev:alice"}


@pytest.fixture
def pf_settings(settings):
    settings.open_prices = False
    settings.payfast_merchant_id = "10000100"
    settings.payfast_merchant_key = "46f0cd694581a"
    settings.payfast_passphrase = "jt7NOE43FZPn"
    settings.payfast_validate = False
    settings.public_url = "https://prices.example"
    return settings


@pytest.fixture
def client(pf_settings):
    c = TestClient(create_app(pf_settings))
    c.post("/api/v1/me/verify-id", json={"id_number": make_id("800101"), "accept_terms": True}, headers=AUTH)
    return c


def itn(settings, m_payment_id, status="COMPLETE", pf_id="1001", amount="99.00", token="tok-abc", **extra):
    pairs = [
        ("m_payment_id", m_payment_id),
        ("pf_payment_id", pf_id),
        ("payment_status", status),
        ("item_name", "PricePulse ZA monthly plan"),
        ("item_description", ""),
        ("amount_gross", amount),
        ("amount_fee", "-5.17"),
        ("amount_net", "93.83"),
        ("name_first", "Test"),
        ("email_address", "a@example.test"),
        ("merchant_id", extra.pop("merchant_id", settings.payfast_merchant_id)),
        ("token", token),
    ]
    pairs.append(
        (
            "signature",
            extra.pop("signature", None)
            or payfast.sign(pairs, settings.payfast_passphrase, skip_empty=False),
        )
    )
    return pairs


def post_itn(client, pairs):
    return client.post(
        "/api/v1/billing/payfast/itn",
        content=urlencode(pairs),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


def test_signature_matches_payfast_rule():
    # MD5 of "a=1&b=hello+world&passphrase=secret" per PayFast's documented method.
    import hashlib

    want = hashlib.md5(b"a=1&b=hello+world&passphrase=secret").hexdigest()
    assert (
        payfast.sign([("a", "1"), ("empty", ""), ("b", " hello world "), ("signature", "x")], "secret")
        == want
    )


def test_checkout_fields(client, pf_settings):
    out = client.post("/api/v1/billing/checkout", headers=AUTH).json()
    f = out["fields"]
    assert out["action_url"] == "https://sandbox.payfast.co.za/eng/process"
    assert f["amount"] == "99.00" and f["recurring_amount"] == "99.00"
    assert f["subscription_type"] == "1" and f["frequency"] == "3" and f["cycles"] == "0"
    assert f["notify_url"] == "https://prices.example/api/v1/billing/payfast/itn"
    assert f["email_address"] == "alice@example.test"
    pairs = [(k, v) for k, v in f.items() if k != "signature"]
    assert f["signature"] == payfast.sign(pairs, pf_settings.payfast_passphrase)
    assert pf_settings.payfast_passphrase not in str(out)


def test_checkout_needs_setup_and_id(settings):
    settings.open_prices = False
    c = TestClient(create_app(settings))
    r = c.post("/api/v1/billing/checkout", headers=AUTH)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "billing_off"
    assert c.get("/api/v1/me", headers=AUTH).json()["billing_ready"] is False


def test_payment_makes_user_paid_and_is_idempotent(client, pf_settings):
    mpid = client.post("/api/v1/billing/checkout", headers=AUTH).json()["fields"]["m_payment_id"]
    assert client.get("/api/v1/me", headers=AUTH).json()["plan"] == "free"
    assert post_itn(client, itn(pf_settings, mpid)).text == "OK"
    me = client.get("/api/v1/me", headers=AUTH).json()
    assert me["plan"] == "paid" and me["subscription_status"] == "active" and me["sessions_per_day"] is None
    first_until = me["paid_until"]
    assert post_itn(client, itn(pf_settings, mpid)).text == "OK"  # same notice again
    assert client.get("/api/v1/me", headers=AUTH).json()["paid_until"] == first_until
    with session_factory(pf_settings.database_url)() as s:
        assert len(s.scalars(select(Payment)).all()) == 1
        assert "Test" not in str([vars(p) for p in s.scalars(select(Payment))])  # no payer details kept


def test_monthly_renewal_extends_by_a_month(pf_settings, session):
    from pricepulse.db import AppUser

    session.add(AppUser(uid="u1"))
    session.add(Subscription(uid="u1", m_payment_id="pp-1"))
    session.flush()
    t0 = datetime(2026, 1, 31, 10, 0)
    assert payfast.handle_itn(session, itn(pf_settings, "pp-1", pf_id="1"), pf_settings, now=t0) == "paid"
    sub = session.scalars(select(Subscription)).one()
    assert sub.paid_until == datetime(2026, 2, 28, 10, 0) + timedelta(days=3)
    t1 = datetime(2026, 2, 28, 9, 0)
    assert (
        payfast.handle_itn(session, itn(pf_settings, "", pf_id="2"), pf_settings, now=t1) == "paid"
    )  # found by token
    assert sub.paid_until == datetime(2026, 3, 28, 10, 0) + timedelta(days=3)


@pytest.mark.parametrize(
    "change,outcome",
    [
        ({"signature": "0" * 32}, "bad_signature"),
        ({"merchant_id": "999"}, "wrong_merchant"),
        ({"amount": "9.90"}, "wrong_amount"),
        ({"status": "FAILED"}, "ignored"),
    ],
)
def test_bad_notices_do_not_grant_access(client, pf_settings, change, outcome):
    mpid = client.post("/api/v1/billing/checkout", headers=AUTH).json()["fields"]["m_payment_id"]
    with session_factory(pf_settings.database_url).begin() as s:
        assert payfast.handle_itn(s, itn(pf_settings, mpid, **change), pf_settings) == outcome
    assert client.get("/api/v1/me", headers=AUTH).json()["plan"] == "free"


def test_tampered_notice_rejected_over_http(client, pf_settings):
    mpid = client.post("/api/v1/billing/checkout", headers=AUTH).json()["fields"]["m_payment_id"]
    pairs = itn(pf_settings, mpid)
    pairs = [(k, "COMPLETE " if k == "payment_status" else v) for k, v in pairs]
    forged = [(k, ("pp-other" if k == "m_payment_id" else v)) for k, v in pairs]
    assert post_itn(client, forged).status_code == 400
    assert post_itn(client, [("m_payment_id", "x")]).status_code == 400


def test_unknown_subscription_and_remote_confirmation(client, pf_settings):
    with session_factory(pf_settings.database_url).begin() as s:
        assert (
            payfast.handle_itn(s, itn(pf_settings, "pp-nope", token=""), pf_settings)
            == "unknown_subscription"
        )
    mpid = client.post("/api/v1/billing/checkout", headers=AUTH).json()["fields"]["m_payment_id"]
    pf_settings.payfast_validate = True
    with session_factory(pf_settings.database_url).begin() as s:
        assert (
            payfast.handle_itn(s, itn(pf_settings, mpid), pf_settings, remote_valid=lambda *_: False)
            == "not_confirmed"
        )

        def boom(*_):
            raise OSError("no network")

        assert (
            payfast.handle_itn(s, itn(pf_settings, mpid), pf_settings, remote_valid=boom) == "confirm_failed"
        )
        assert (
            payfast.handle_itn(s, itn(pf_settings, mpid), pf_settings, remote_valid=lambda *_: True) == "paid"
        )


def test_cancel_keeps_access_until_paid_up(client, pf_settings):
    mpid = client.post("/api/v1/billing/checkout", headers=AUTH).json()["fields"]["m_payment_id"]
    post_itn(client, itn(pf_settings, mpid))
    assert post_itn(client, itn(pf_settings, mpid, status="CANCELLED", pf_id="")).text == "OK"
    me = client.get("/api/v1/me", headers=AUTH).json()
    assert me["subscription_status"] == "cancelled" and me["plan"] == "paid"
    with session_factory(pf_settings.database_url).begin() as s:
        s.scalars(select(Subscription)).one().paid_until = utcnow() - timedelta(days=1)
    me = client.get("/api/v1/me", headers=AUTH).json()
    assert me["plan"] == "free" and me["sessions_per_day"] == 3


def test_lapsed_payment_drops_back_to_free(client, pf_settings):
    mpid = client.post("/api/v1/billing/checkout", headers=AUTH).json()["fields"]["m_payment_id"]
    post_itn(client, itn(pf_settings, mpid))
    with session_factory(pf_settings.database_url).begin() as s:
        s.scalars(select(Subscription)).one().paid_until = utcnow() - timedelta(hours=1)
    assert client.get("/api/v1/me", headers=AUTH).json()["plan"] == "free"


def test_resubscribing_after_cancel_keeps_paid_time(client, pf_settings):
    mpid = client.post("/api/v1/billing/checkout", headers=AUTH).json()["fields"]["m_payment_id"]
    post_itn(client, itn(pf_settings, mpid))
    post_itn(client, itn(pf_settings, mpid, status="CANCELLED", pf_id=""))
    new = client.post("/api/v1/billing/checkout", headers=AUTH)  # taps Upgrade again
    assert new.status_code == 200
    assert client.get("/api/v1/me", headers=AUTH).json()["plan"] == "paid"
    post_itn(client, itn(pf_settings, new.json()["fields"]["m_payment_id"], pf_id="2002", token="tok-new"))
    assert client.get("/api/v1/me", headers=AUTH).json()["subscription_status"] == "active"
