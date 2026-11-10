from pricepulse.trolleyup import ExportItem, short_name, store_name, to_csv


def test_names_fit_trolleyup_rules():
    cases = [
        ("Clover Full Cream Milk", "2L"),
        ("Castle Lite", "24 x 340ml"),
        ("Bananas", "per kg"),
        ("Huggies Gold Nappies Size 4 Jumbo Value Pack Extra Soft", "62s"),
        ("A" * 60, "1kg"),
        ("See www.example.com now 0821234567", ""),
    ]
    for name, size in cases:
        out = short_name(name, size)
        assert 1 <= len(out) <= 40 and len(out.split()) <= 7, out
        assert "www." not in out and "0821234567" not in out
    assert short_name("Clover Full Cream Milk", "2L") == "Clover Full Cream Milk 2L"
    assert short_name("Castle Lite", "24 x 340ml") == "Castle Lite 24x340ml"
    assert short_name("Clover Milk 2L", "2L") == "Clover Milk 2L"  # size not repeated


def test_liquor_store_names():
    assert store_name("Checkers", "Liquor") == "Checkers LiquorShop"
    assert store_name("Pick n Pay", "Liquor") == "Pick n Pay Liquor"
    assert store_name("Checkers", "Fresh") == "Checkers"
    assert store_name("Ultra Liquors", "Liquor") == "Ultra Liquors"


def test_csv_shape():
    text = to_csv(
        [
            ExportItem("=Evil", "1L", "Groceries", 500, "Checkers", 10.0),
            ExportItem("Bananas", "per kg", "Fresh", 0, "", None),
        ],
        "10 Nov",
    )
    assert text.startswith("﻿Item,Quantity,Store,Note\r\n")
    lines = text.strip().split("\r\n")
    assert lines[1] == "'=Evil 1L,99,Checkers,PricePulse price R 10.00 on 10 Nov"
    assert lines[2] == "Bananas per kg,1,,"
    assert (
        len(to_csv([ExportItem("X", "1L", "Fresh", 1, "", None)] * 150, "1 Jan").strip().split("\r\n")) == 101
    )
