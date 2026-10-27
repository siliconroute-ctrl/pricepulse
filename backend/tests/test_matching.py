"""Matching regression tests. tests/data/match_pairs.csv holds the real-looking pairs."""

import csv
from pathlib import Path

import pytest

from pricepulse.matching import Candidate, detect_brand, match, normalise, same_product

PAIRS = list(csv.DictReader((Path(__file__).parent / "data" / "match_pairs.csv").open(encoding="utf-8")))


def test_enough_pairs():
    assert len(PAIRS) >= 40
    assert sum(p["expected"] == "match" for p in PAIRS) >= 15
    assert sum(p["expected"] == "nomatch" for p in PAIRS) >= 15


@pytest.mark.parametrize("pair", PAIRS, ids=[f"{p['expected']}:{p['name_a']}~{p['name_b']}" for p in PAIRS])
def test_pair(pair):
    got = same_product((pair["name_a"], pair["size_a"]), (pair["name_b"], pair["size_b"]))
    assert got == (pair["expected"] == "match"), pair.get("note")


def test_brand_detection():
    assert detect_brand("Coca-Cola Original 2L") == "coca cola"
    assert detect_brand("Coke No Sugar") == "coca cola"
    assert detect_brand("Castle Lite 6 x 330ml") == "castle lite"
    assert detect_brand("Castle Lager") == "castle"
    assert detect_brand("Something Else", brand_hint="Clover") == "clover"
    assert detect_brand("Large Eggs") == ""


def test_marketing_words_removed():
    assert normalise("Clover Fresh Full Cream Milk NEW Value Pack", "2L").name == "cream full milk"


def test_match_flow_exact_fuzzy_review_new():
    milk = Candidate(id=1, norm=normalise("Clover Full Cream Milk", "2L"), stores={"Checkers"})
    assert match(normalise("CLOVER FULL CREAM MILK 2L"), [milk], store="Woolworths").status == "exact"
    beer = Candidate(id=2, norm=normalise("Castle Lite", "24 x 340ml"), stores={"Checkers"})
    r = match(normalise("Castle Lite Beer Cans", "24 x 340ml"), [beer], store="Pick n Pay")
    assert (r.status, r.product_id) == ("fuzzy", 2)
    r = match(normalise("Clover Full Cream Milk", "1L"), [milk], store="Woolworths")
    assert r.status == "new"
    r = match(normalise("Mystery Thing"), [milk])
    assert r.status == "needs_review"  # no size


def test_review_band():
    base = Candidate(id=3, norm=normalise("Albany Superior White Bread", "700g"))
    r = match(
        normalise("Albany Superior Brown Bread", "700g"), [base], auto_threshold=95, review_threshold=60
    )
    assert r.status == "needs_review" and r.suggestion_id == 3


def test_same_store_does_not_merge_two_different_listings():
    c = Candidate(id=4, norm=normalise("Castle Lite", "24 x 340ml"), stores={"Checkers"})
    r = match(normalise("Castle Lite Beer", "24 x 340ml"), [c], store="Checkers")
    assert r.status == "new"
