"""Load regions.yaml and answer distance questions."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

REGION_RE = re.compile(r"^[a-z0-9_-]{1,40}$")


@dataclass
class Region:
    """One town the app can price."""

    slug: str
    name: str
    lat: float
    lon: float
    postal_code: str = ""
    stores: dict[str, dict] = field(default_factory=dict)


@dataclass
class RegionConfig:
    """Everything in regions.yaml."""

    regions: dict[str, Region]
    store_pricing: dict[str, str]

    def pricing_mode(self, store: str) -> str:
        return self.store_pricing.get(store, "unknown")

    def store_setting(self, region: str, store: str) -> dict:
        r = self.regions.get(region)
        return dict(r.stores.get(store, {})) if r else {}


def valid_region(slug: str) -> bool:
    """Region names are 1-40 characters of a-z, 0-9, _ and -."""
    return bool(REGION_RE.fullmatch(slug or ""))


@lru_cache(maxsize=4)
def load_regions(path: str | Path) -> RegionConfig:
    """Read regions.yaml. Cached; pass the path so tests can use their own file."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    regions = {}
    for slug, r in (raw.get("regions") or {}).items():
        if not valid_region(slug):
            raise ValueError(f"Bad region name in regions.yaml: {slug!r}")
        regions[slug] = Region(
            slug=slug,
            name=r.get("name", slug.title()),
            lat=float(r["lat"]),
            lon=float(r["lon"]),
            postal_code=str(r.get("postal_code", "")),
            stores=r.get("stores") or {},
        )
    pricing = {
        name: (cfg or {}).get("regional_pricing", "unknown")
        for name, cfg in (raw.get("stores") or {}).items()
    }
    return RegionConfig(regions=regions, store_pricing=pricing)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance in kilometres."""
    r = math.radians
    dlat, dlon = r(lat2 - lat1), r(lon2 - lon1)
    h = math.sin(dlat / 2) ** 2 + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))
