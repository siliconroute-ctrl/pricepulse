"""Email alerts for failed stores: no real mail is sent, smtplib is replaced."""

from datetime import UTC, datetime, timedelta

import pytest

from pricepulse import alerts
from pricepulse.scrapers.base import StoreHealth

ENV = {
    "PP_ALERT_SMTP_HOST": "mail.example.test",
    "PP_ALERT_SMTP_USER": "me@example.test",
    "PP_ALERT_SMTP_PASSWORD": "not-a-real-password",
    "PP_ALERT_TO": "owner@example.test",
}


class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=0, context=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        self.login_used = (user, password)

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


@pytest.fixture
def mail(monkeypatch):
    FakeSMTP.sent = []
    for k in list(ENV):
        monkeypatch.setenv(k, ENV[k])
    monkeypatch.setattr(alerts.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(alerts.smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


def health(store, status="ok", items=3):
    h = StoreHealth(store=store, region="paarl", terms_total=3, terms_found=3 if items else 0, items=items)
    h.status = status
    return h


def test_only_broken_stores_are_problems():
    ok = health("Checkers")
    bad = health("Woolworths", "failed", 0)
    blocked = health("Pick n Pay", "blocked", 0)
    partial = health("Dis-Chem", "partial", 2)
    assert [h.store for h in alerts.problems([ok, bad, blocked, partial])] == ["Woolworths", "Pick n Pay"]


def test_email_names_the_store_and_what_to_do(mail, tmp_path):
    assert alerts.alert_if_needed([health("Woolworths", "failed", 0)], tmp_path / "s.json", "13 Nov")
    msg = mail.sent[0]
    assert "Woolworths" in msg["Subject"] and msg["To"] == "owner@example.test"
    body = msg.get_content()
    assert "--save-fixtures" in body and "nothing was invented" in body
    assert "not-a-real-password" not in body


def test_same_store_is_not_repeated_within_a_day(mail, tmp_path):
    f = tmp_path / "s.json"
    bad = [health("Woolworths", "failed", 0)]
    t0 = datetime(2026, 11, 13, 10, tzinfo=UTC)
    assert alerts.alert_if_needed(bad, f, "x", now=t0)
    assert not alerts.alert_if_needed(bad, f, "x", now=t0 + timedelta(hours=5))
    assert alerts.alert_if_needed(bad, f, "x", now=t0 + timedelta(hours=25))
    assert len(mail.sent) == 2


def test_a_store_that_recovers_alerts_again_at_once(mail, tmp_path):
    f = tmp_path / "s.json"
    t0 = datetime(2026, 11, 13, 10, tzinfo=UTC)
    alerts.alert_if_needed([health("Woolworths", "failed", 0)], f, "x", now=t0)
    alerts.alert_if_needed([health("Woolworths")], f, "x", now=t0 + timedelta(hours=1))  # works again
    assert alerts.alert_if_needed([health("Woolworths", "failed", 0)], f, "x", now=t0 + timedelta(hours=2))


def test_nothing_is_sent_when_all_is_well(mail, tmp_path):
    assert not alerts.alert_if_needed([health("Checkers")], tmp_path / "s.json", "x")
    assert mail.sent == []


def test_not_set_up_means_no_email_and_no_crash(monkeypatch, tmp_path, capsys):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    assert not alerts.configured()
    assert not alerts.alert_if_needed([health("Woolworths", "failed", 0)], tmp_path / "s.json", "x")
    assert "not set up" in capsys.readouterr().out


def test_mail_server_trouble_never_raises(mail, tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("no route")

    monkeypatch.setattr(alerts.smtplib, "SMTP", boom)
    assert not alerts.send_email("s", "b")
    assert "Could not send" in capsys.readouterr().out
