"""No bots: an account that looks automated is paused (every plan), the admin can lift it."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_accounts_api import add, auth, ids, seed, verify

from pricepulse import abuse
from pricepulse.api.app import create_app
from pricepulse.config import get_settings
from pricepulse.db import AbuseLock, AppUser, session_factory, utcnow


@pytest.fixture
def client(settings, monkeypatch):
    settings.open_prices = False
    monkeypatch.setenv("PP_ABUSE_LOOKUPS", "20")
    monkeypatch.setenv("PP_ABUSE_SEARCHES", "90")
    monkeypatch.setenv("PP_ABUSE_RHYTHM", "50")
    abuse.reset()
    seed(settings)
    return TestClient(create_app(get_settings()))


def set_plan(plan):
    s = get_settings()
    with session_factory(s.database_url).begin() as db:
        db.scalars(select(AppUser)).first().plan_override = plan


def test_human_speed_never_locks(client):
    verify(client)
    pid = ids(client)["Clover Full Cream Milk"]
    for _ in range(5):
        assert add(client, pid).status_code == 200


def test_a_burst_of_lookups_pauses_the_account_even_when_paid(client):
    verify(client)
    set_plan("paid")
    pid = ids(client)["Clover Full Cream Milk"]
    codes = [add(client, pid).status_code for _ in range(25)]
    assert 429 in codes and codes[:20] == [200] * 20
    r = add(client, pid)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "locked"
    assert "not scripts" in r.json()["detail"]["message"]
    # a paused account can still sign in and read its own details
    assert client.get("/api/v1/me", headers=auth()).status_code == 200
    # the pause also covers the catalogue
    assert client.get("/api/v1/catalogue", params={"q": "milk"}, headers=auth()).status_code == 429


def test_the_pause_ends_and_a_second_one_lasts_a_day(client):
    verify(client)
    pid = ids(client)["Clover Full Cream Milk"]
    for _ in range(22):
        add(client, pid)
    s = get_settings()
    with session_factory(s.database_url).begin() as db:
        row = db.scalars(select(AbuseLock)).one()
        assert timedelta(minutes=29) < row.locked_until - utcnow() < timedelta(minutes=31)
        row.locked_until = utcnow() - timedelta(minutes=1)  # the 30 minutes are over
    abuse.reset()
    assert add(client, pid).status_code == 200
    for _ in range(22):
        add(client, pid)
    with session_factory(s.database_url)() as db:
        newest = db.scalars(select(AbuseLock).order_by(AbuseLock.id.desc())).first()
        assert newest.locked_until - utcnow() > timedelta(hours=23)


def test_a_machine_rhythm_pauses_the_account(client):
    verify(client)
    s = get_settings()
    with session_factory(s.database_url).begin() as db:
        user = db.scalars(select(AppUser)).first()
        for i in range(60):  # one call every 2 seconds, exactly
            try:
                abuse.guard(db, user, "search", now=1000.0 + i * 2.0)
            except Exception as exc:  # noqa: BLE001
                assert exc.code == "locked" and i >= 49
                return
    raise AssertionError("a perfectly regular rhythm was not caught")


def test_testers_and_admins_are_not_paused_automatically(client):
    verify(client)
    pid = ids(client)["Clover Full Cream Milk"]
    set_plan("tester")
    assert all(add(client, pid).status_code == 200 for _ in range(25))


def test_admin_can_see_and_lift_a_pause(client):
    verify(client)
    pid = ids(client)["Clover Full Cream Milk"]
    for _ in range(22):
        add(client, pid)
    s = get_settings()
    with session_factory(s.database_url).begin() as db:
        db.scalars(select(AppUser)).first()
        uid = db.scalars(select(AppUser)).first().uid
        # a second account is the admin
    client.get("/api/v1/me", headers=auth("boss"))
    with session_factory(s.database_url).begin() as db:
        boss = [u for u in db.scalars(select(AppUser)) if u.uid != uid][0]
        boss.is_admin = True
    users = client.get("/api/v1/admin/users", headers=auth("boss")).json()["users"]
    assert [u for u in users if u["uid"] == uid][0]["locked_until"]
    assert client.get("/api/v1/admin/overview", headers=auth("boss")).json()["locked"] == 1
    r = client.post(f"/api/v1/admin/users/{uid}/unlock", headers=auth("boss"))
    assert r.status_code == 200 and r.json()["released"] == 1
    assert add(client, pid).status_code == 200
    assert client.post(f"/api/v1/admin/users/{uid}/unlock", headers=auth()).status_code == 404  # not an admin
