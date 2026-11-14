"""The pre-upload file check (no network, no server)."""

import json
from datetime import datetime, timedelta, timezone

from upload_check import check_file

STORES = ["Checkers", "Pick n Pay", "Woolworths", "Dis-Chem"]


def stamp(hours_ago=1):
    t = datetime.now(timezone(timedelta(hours=2))) - timedelta(hours=hours_ago)
    return t.strftime("%Y-%m-%d %H:%M")


def write(tmp_path, **over):
    rows = [{"store": s, "name": "x", "price": 10.0} for s in STORES]
    data = {"region": "paarl", "scraped_at": stamp(), "items": rows} | over
    p = tmp_path / "latest_paarl.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_a_fresh_complete_file_is_good(tmp_path):
    problems, warnings, per = check_file(write(tmp_path), 36, STORES)
    assert problems == [] and warnings == [] and per["Checkers"] == 1


def test_missing_empty_or_broken_files_are_problems(tmp_path):
    assert check_file(tmp_path / "nope.json", 36, STORES)[0]
    assert check_file(write(tmp_path, items=[]), 36, STORES)[0]
    bad = tmp_path / "latest_x.json"
    bad.write_text("{not json", encoding="utf-8")
    assert "cannot be read" in check_file(bad, 36, STORES)[0][0]


def test_old_example_and_undated_files_are_refused(tmp_path):
    assert "older than" in check_file(write(tmp_path, scraped_at=stamp(60)), 36, STORES)[0][0]
    assert "EXAMPLE" in check_file(write(tmp_path, sample=True), 36, STORES)[0][0]
    assert "no date" in check_file(write(tmp_path, scraped_at=""), 36, STORES)[0][0]


def test_a_missing_store_is_a_note_not_a_block(tmp_path):
    rows = [{"store": "Checkers", "price": 5.0}]
    problems, warnings, _ = check_file(write(tmp_path, items=rows), 36, STORES)
    assert problems == [] and any("Woolworths" in w for w in warnings)
