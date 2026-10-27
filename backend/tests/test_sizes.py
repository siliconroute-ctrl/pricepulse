import pytest

from pricepulse.sizes import parse_size, split_size


@pytest.mark.parametrize(
    "text,key",
    [
        ("2L", "1x2000ml"),
        ("2 l", "1x2000ml"),
        ("2ltr", "1x2000ml"),
        ("500ml", "1x500ml"),
        ("1.5kg", "1x1500g"),
        ("1,5kg", "1x1500g"),
        ("410g", "1x410g"),
        ("24 x 340ml", "24x340ml"),
        ("6 x 330ml NRB", "6x330ml"),
        ("340ml x 24", "24x340ml"),
        ("6x330ml", "6x330ml"),
        ("18 pack", "1x18each"),
        ("24 tablets", "1x24each"),
        ("18s", "1x18each"),
        ("100's", "1x100each"),
        ("per kg", "per-kg"),
        ("75cl", "1x750ml"),
        ("6 pack 330ml", "6x330ml"),
        ("24 tablets 500mg", "1x24each"),
        ("each", "1x1each"),
        ("5 L", "1x5000ml"),
    ],
)
def test_parse_size(text, key):
    assert parse_size(text).key == key


@pytest.mark.parametrize("text", ["", None, "large", "assorted"])
def test_parse_size_none(text):
    assert parse_size(text) is None


def test_unit_prices():
    assert parse_size("2L").unit_price(36.99) == (1.85, "per 100ml")
    assert parse_size("410g").unit_price(14.99) == (3.66, "per 100g")
    assert parse_size("18 pack").unit_price(69.99) == (3.89, "per item")
    assert parse_size("per kg").unit_price(24.99) == (2.5, "per 100g")
    assert parse_size("24 x 340ml").unit_price(329.99) == (4.04, "per 100ml")


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Clover Full Cream Milk 2L", ("Clover Full Cream Milk", "2L")),
        ("Castle Lite 24 x 340ml", ("Castle Lite", "24 x 340ml")),
        ("Bananas per kg", ("Bananas", "per kg")),
        ("Five Roses Tea 100s", ("Five Roses Tea", "100s")),
        ("Just A Name", ("Just A Name", "")),
    ],
)
def test_split_size(name, expected):
    assert split_size(name) == expected
