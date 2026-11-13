"""One small file per store to send to Claude: results, health and the price feeds.

The browser's file chooser struggles with many files; this makes one. It is plain text, holds
only public shop data, and is rebuilt on every run with --save-fixtures.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .scrapers.base import looks_like_products, split_feeds

MAX_FEEDS = 3
MAX_FEED_CHARS = 150_000


def _short(url_line: str) -> str:
    return re.sub(r"\?.*?(\s+\(\d+ characters\))", r"?...\1", url_line)[:200]


def write_bundle(
    out_dir: Path,
    name: str,
    slug: str,
    region: str,
    summary: str,
    items: list[dict],
    source_dirs: list[Path],
    since: float,
) -> Path:
    """Write ``{out_dir}/{slug}.txt`` and return its path."""
    feeds: list[tuple[str, str]] = []
    for folder in source_dirs:
        for path in sorted(folder.glob("*.network.txt")) if folder.is_dir() else []:
            if path.stat().st_mtime >= since:
                feeds += split_feeds(path.read_text(encoding="utf-8", errors="replace"))
    product_feeds = [f for f in feeds if looks_like_products(f[1])]
    product_feeds.sort(key=lambda f: len(f[1]), reverse=True)
    chosen, seen = [], set()
    for header, body in product_feeds:
        key = header.split("?")[0] + body[:200]
        if key not in seen and len(chosen) < MAX_FEEDS:
            seen.add(key)
            chosen.append((header, body))
    lines = [
        f"PricePulse bundle for {name} ({region})",
        "",
        "RESULT: " + summary,
        "",
        f"ITEMS READ: {len(items)}",
        *[json.dumps(i, ensure_ascii=False) for i in items[:40]],
        "",
        f"PRICE FEEDS ({len(chosen)} of {len(product_feeds)}, each cut at {MAX_FEED_CHARS} characters):",
    ]
    for header, body in chosen:
        lines += ["", header, body[:MAX_FEED_CHARS]]
    others = [h for h, _ in feeds if (h, _) not in chosen]
    lines += ["", f"OTHER ANSWERS THE PAGE LOADED ({len(others)}), addresses only:"]
    lines += sorted({_short(h) for h in others})[:80]
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{slug}.txt"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target
