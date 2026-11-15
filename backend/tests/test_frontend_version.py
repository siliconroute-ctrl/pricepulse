"""The app version shown in the menu must equal the service worker's version number."""

import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def test_app_version_matches_the_service_worker():
    page = (FRONTEND / "index.html").read_text(encoding="utf-8")
    sw = (FRONTEND / "sw.js").read_text(encoding="utf-8")
    app = re.search(r"var APP_VERSION = (\d+);", page)
    cache = re.search(r'var VERSION = "pp-shell-v(\d+)"', sw)
    assert app and cache, "APP_VERSION in index.html and VERSION in sw.js must both exist"
    assert app.group(1) == cache.group(1), "raise APP_VERSION and the sw.js number together"


def test_service_worker_never_answers_sw_js():
    sw = (FRONTEND / "sw.js").read_text(encoding="utf-8")
    assert r"sw\.js$" in sw  # so App update can read the newest version straight from the server


def test_menu_has_the_required_items():
    page = (FRONTEND / "index.html").read_text(encoding="utf-8")
    for needed in (
        'id="menubtn"',
        'aria-label="Menu"',
        "wa.me",
        "privacy.html",
        "terms.html",
        'data-menu="update"',
        'data-menu="sharelink"',
    ):
        assert needed in page, needed
