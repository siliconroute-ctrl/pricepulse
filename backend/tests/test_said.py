from datetime import date

import pytest
from conftest import make_id

from pricepulse.said import check_id, fingerprint

TODAY = date(2026, 11, 10)


def test_adult_and_minor():
    adult = check_id(make_id("800101"), TODAY)
    assert adult.valid and adult.birth_date == date(1980, 1, 1) and adult.age_on(TODAY) == 46
    minor = check_id(make_id("100315"), TODAY)
    assert minor.valid and minor.age_on(TODAY) == 16 and minor.adult_on() == date(2028, 3, 15)


def test_eighteenth_birthday_edge():
    assert check_id(make_id("081110"), TODAY).age_on(TODAY) == 18  # 18 today
    assert check_id(make_id("081111"), TODAY).age_on(TODAY) == 17  # 18 tomorrow
    assert check_id(make_id("080229"), TODAY).adult_on() == date(2026, 3, 1)  # leap-day birthday


def test_century():
    assert check_id(make_id("270101"), TODAY).birth_date == date(1927, 1, 1)
    assert check_id(make_id("261110"), TODAY).birth_date == date(2026, 11, 10)
    assert check_id(make_id("261111"), TODAY).birth_date == date(1926, 11, 11)


def test_spaces_and_dashes_allowed():
    n = make_id("800101")
    assert check_id(f"{n[:6]} {n[6:10]} {n[10:]}", TODAY).valid
    assert check_id(f"{n[:6]}-{n[6:10]}-{n[10:]}", TODAY).valid


@pytest.mark.parametrize(
    "bad", ["", "123", "abcdefghijklm", "8001015009086", "9902305009084", "80010150090870"]
)
def test_invalid(bad):
    r = check_id(bad, TODAY)
    assert not r.valid and r.problem


def test_citizenship_digit():
    n = make_id("800101", citizen="1")
    assert check_id(n, TODAY).valid
    assert not check_id(n[:10] + "5" + n[11:], TODAY).valid


def test_fingerprint_is_keyed_and_stable():
    n = make_id("800101")
    a = fingerprint(n, "secret-one-secret-one")
    assert a == fingerprint(f"{n[:6]} {n[6:]}", "secret-one-secret-one")
    assert a != fingerprint(n, "secret-two-secret-two")
    assert n not in a and len(a) == 64
    with pytest.raises(ValueError):
        fingerprint(n, "short")
