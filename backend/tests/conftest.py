"""Shared test setup: every test gets its own temporary data folder and database."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("PP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("PP_DATABASE_URL", raising=False)
    monkeypatch.setenv("PP_ALLOWED_ORIGINS", "https://pricepulse.example")
    monkeypatch.setenv("PP_RATE_LIMIT_PER_MIN", "1000")
    # The older engine tests use the open whole-catalogue endpoints; the app itself does not.
    monkeypatch.setenv("PP_OPEN_PRICES", "1")
    monkeypatch.setenv("PP_AUTH_DEV", "1")
    monkeypatch.setenv("PP_ID_SECRET", "test-secret-test-secret-0123456789")
    for k in ("PP_PAYFAST_MERCHANT_ID", "PP_PAYFAST_MERCHANT_KEY", "PP_PAYFAST_PASSPHRASE"):
        monkeypatch.delenv(k, raising=False)
    from pricepulse.config import get_settings

    return get_settings()


@pytest.fixture
def session(settings):
    from pricepulse.db import session_factory

    Session = session_factory(settings.database_url)
    with Session() as s:
        yield s
        s.commit()


def row(store, name, price, size="", category="Groceries", promo=None, url=""):
    r = {"store": store, "name": name, "size": size, "category": category, "price": price, "url": url}
    if promo is not None:
        r["promo_price"] = promo
    return r


def make_id(yymmdd: str, seq: str = "5009", citizen: str = "0") -> str:
    """Build a valid (made-up) South African ID number for a date of birth."""
    from pricepulse.said import luhn_ok

    base = yymmdd + seq + citizen + "8"
    return next(base + z for z in "0123456789" if luhn_ok(base + z))
