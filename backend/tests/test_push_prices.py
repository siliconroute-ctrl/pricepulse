"""The hands-off upload client, against a small fake server on this machine."""

import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import push_prices

TOKEN = "k" * 40
STORES = ["Checkers", "Pick n Pay", "Woolworths", "Dis-Chem"]


class Fake(BaseHTTPRequestHandler):
    seen: list = []
    status = 200

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("content-length", 0))
        body = self.rfile.read(n)
        Fake.seen.append((self.path, self.headers.get("Authorization"), json.loads(body)["region"]))
        code = Fake.status if self.headers.get("Authorization") == f"Bearer {TOKEN}" else 401
        data = json.dumps({"saved": 4, "by_barcode": 1} if code == 200 else {"detail": "no"}).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # silence
        pass


@pytest.fixture
def server():
    Fake.seen, Fake.status = [], 200
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def files(settings):
    from pricepulse.regions import load_regions

    stamp = (datetime.now(timezone(timedelta(hours=2))) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
    for slug in load_regions(str(settings.regions_file)).regions:
        rows = [{"store": s, "name": "x", "price": 10.0} for s in STORES]
        (settings.data_dir / f"latest_{slug}.json").write_text(
            json.dumps({"region": slug, "scraped_at": stamp, "items": rows}), encoding="utf-8"
        )
    return settings


def test_token_comes_from_the_environment_or_a_private_file(tmp_path):
    f = tmp_path / "tok"
    f.write_text(TOKEN + "\nsecond line ignored\n")
    assert push_prices.read_token({"PP_UPLOAD_TOKEN": " abc "}, f) == "abc"
    assert push_prices.read_token({}, f) == TOKEN
    assert push_prices.read_token({}, tmp_path / "missing") == ""


def test_all_seven_towns_are_uploaded_with_the_token(files, server, monkeypatch, capsys):
    monkeypatch.setenv("PP_UPLOAD_TOKEN", TOKEN)
    assert push_prices.main(["--url", server]) == 0
    assert len(Fake.seen) == 7 and all(p == "/api/v1/ingest/prices" for p, _, _ in Fake.seen)
    assert {a for _, a, _ in Fake.seen} == {f"Bearer {TOKEN}"}
    out = capsys.readouterr().out
    assert "All towns loaded." in out and TOKEN not in out


def test_a_refused_upload_is_reported_not_hidden(files, server, monkeypatch, capsys):
    monkeypatch.setenv("PP_UPLOAD_TOKEN", "wrong" * 8)
    assert push_prices.main(["--url", server]) == 1
    assert "NOT loaded" in capsys.readouterr().out


def test_no_token_means_nothing_is_sent(files, server, monkeypatch, tmp_path):
    monkeypatch.delenv("PP_UPLOAD_TOKEN", raising=False)
    monkeypatch.setattr(push_prices, "TOKEN_FILE", tmp_path / "none")
    assert push_prices.main(["--url", server]) == 1 and Fake.seen == []


def test_stale_files_are_not_sent(files, server, monkeypatch):
    monkeypatch.setenv("PP_UPLOAD_TOKEN", TOKEN)
    assert push_prices.main(["--url", server, "--max-hours", "0.01"]) == 1
    assert Fake.seen == []


def test_server_errors_are_retried_then_reported(monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise OSError("down")

    monkeypatch.setattr(push_prices.urllib.request, "urlopen", boom)
    monkeypatch.setattr(push_prices.time, "sleep", lambda s: None)
    status, text = push_prices.send_file("http://x.invalid", TOKEN, b"{}", tries=3)
    assert status == 0 and "could not reach" in text and len(calls) == 3
