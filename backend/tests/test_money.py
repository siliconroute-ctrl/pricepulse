import pytest

from pricepulse.money import clean_price, format_rand, from_cents, is_suspect, to_cents


@pytest.mark.parametrize(
    "text,expected",
    [
        ("R36.99", 36.99),
        ("R 1 249,99", 1249.99),
        ("R1,249.99", 1249.99),
        ("36.99", 36.99),
        (36.99, 36.99),
        ("R36\n99", 36.99),
        ("R 129", 129.0),
        ("Now R19.99 each", 19.99),
        ("R1 249", 1249.0),
        ("", None),
        (None, None),
        ("R0.00", None),
        (0, None),
        (-5, None),
        ("Out of stock", None),
    ],
)
def test_clean_price(text, expected):
    assert clean_price(text) == expected


def test_cents_round_trip():
    assert to_cents(36.99) == 3699
    assert to_cents(0.1 + 0.2) == 30
    assert from_cents(3699) == 36.99
    assert to_cents(None) is None and from_cents(None) is None


def test_format_rand():
    assert format_rand(1249.99) == "R 1 249.99"
    assert format_rand(5.5) == "R 5.50"
    assert format_rand(1234567) == "R 1 234 567.00"


def test_suspect():
    assert is_suspect(0.5) and is_suspect(5000.01)
    assert not is_suspect(1) and not is_suspect(4999)
