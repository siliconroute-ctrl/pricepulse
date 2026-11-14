import os
import time

from pricepulse.bundle import write_bundle


def test_one_file_with_results_and_only_the_price_feeds(tmp_path):
    live = tmp_path / "live" / "shop"
    live.mkdir(parents=True)
    product = (
        '{"products":[{"name":"Milk","price":10.5},{"name":"Bread","price":9.5},{"name":"Eggs","price":50}]}'
    )
    (live / "milk.network.txt").write_text(
        f"=== 200 https://shop.example/api/search?q=milk&key=SECRETISH  ({len(product)} characters)\n{product}\n\n"
        '=== 200 https://shop.example/api/menu  (40 characters)\n{"links":[{"name":"a","url":"/b"}]}\n',
        encoding="utf-8",
    )
    old = live / "old.network.txt"
    old.write_text("=== 200 https://shop.example/old  (9 characters)\n" + product, encoding="utf-8")
    os.utime(old, (time.time() - 3600, time.time() - 3600))  # from an earlier run: must be left out

    out = write_bundle(
        tmp_path / "send",
        "Shop",
        "shop",
        "paarl",
        "Shop: 1 of 1 products found.",
        [{"store": "Shop", "name": "Milk", "price": 10.5}],
        [live, tmp_path / "missing"],
        since=time.time() - 60,
    )
    text = out.read_text(encoding="utf-8")
    assert out.name == "shop.txt" and out.parent.name == "send"
    assert "RESULT: Shop: 1 of 1 products found." in text and "ITEMS READ: 1" in text
    assert '"Milk"' in text and product in text
    assert "https://shop.example/old" not in text  # older than this run
    assert "OTHER ANSWERS THE PAGE LOADED (1)" in text and "https://shop.example/api/menu" in text
    assert "SECRETISH" not in text.split("OTHER ANSWERS")[1]  # addresses in the list drop the query part
