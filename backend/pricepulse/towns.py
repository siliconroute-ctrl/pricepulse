"""Towns a local store can be in, and which shopper towns (regions) a store is shown in.

A store is shown to shoppers whose town is within ``radius_km`` (default 100) of the store's town.
Prices are written once for every shopper town in range, so the normal price queries need no change.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

import yaml

from .regions import haversine_km, valid_region

DEFAULT_RADIUS_KM = 100.0


@dataclass(frozen=True)
class Town:
    slug: str
    name: str
    province: str
    lat: float
    lon: float


@functools.lru_cache(maxsize=4)
def load_towns(path: str) -> dict[str, Town]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out: dict[str, Town] = {}
    for slug, t in (raw.get("towns") or {}).items():
        if not valid_region(slug):
            raise ValueError(f"Bad town name in towns.yaml: {slug!r}")
        out[slug] = Town(slug, t["name"], t.get("province", ""), float(t["lat"]), float(t["lon"]))
    return out


def regions_in_range(lat: float, lon: float, regions, radius_km: float = DEFAULT_RADIUS_KM) -> list[str]:
    """Shopper towns within the radius, nearest first."""
    near = sorted((haversine_km(lat, lon, r.lat, r.lon), slug) for slug, r in regions.regions.items())
    return [slug for d, slug in near if d <= radius_km]


def price_regions(store, regions, radius_km: float = DEFAULT_RADIUS_KM) -> list[str]:
    """The shopper towns a store's prices are written for. A store without a map point (set by hand
    in Admin) keeps its single region; a store out of range of every shopper town is written under
    its own town name, which no shopper has, so it stays unseen until a nearby town is added."""
    if store.lat is None or store.lon is None or (store.lat == 0 and store.lon == 0):
        return [store.region] if store.region else []
    near = regions_in_range(store.lat, store.lon, regions, radius_km)
    if near:
        return near
    return [store.region or "none"]
