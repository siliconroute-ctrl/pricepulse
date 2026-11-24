"""Towns inside the nine provinces, and which local stores a shopper can see.

A region is a province (regions.yaml). A town (towns.yaml) belongs to one province. A shopper picks a
province and, optionally, a town; with no town the province capital is used. A local store is shown to
a shopper when the store's town is within ``radius_km`` (default 100) of the shopper's town.
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
    province: str  # a region slug from regions.yaml
    lat: float
    lon: float


@functools.lru_cache(maxsize=4)
def load_towns(path: str) -> dict[str, Town]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out: dict[str, Town] = {}
    for slug, t in (raw.get("towns") or {}).items():
        if not valid_region(slug):
            raise ValueError(f"Bad town name in towns.yaml: {slug!r}")
        out[slug] = Town(slug, t["name"], str(t["province"]), float(t["lat"]), float(t["lon"]))
    return out


def shopper_point(regions, towns: dict[str, Town], region: str, town: str | None) -> tuple[float, float, str]:
    """The shopper's map point and town slug: the chosen town (if it is in this province), else the
    province capital's point (town slug "")."""
    t = towns.get(town or "")
    if t is not None and t.province == region:
        return t.lat, t.lon, t.slug
    r = regions.regions[region]
    return r.lat, r.lon, ""


def near_store_ids(stores, lat: float, lon: float, region: str, radius_km: float) -> frozenset[int]:
    """Ids of local stores within the radius. A store with no map point is seen only in its own region."""
    out = set()
    for st in stores:
        if st.lat is None or st.lon is None:
            if st.region == region:
                out.add(st.id)
        elif haversine_km(lat, lon, st.lat, st.lon) <= radius_km:
            out.add(st.id)
    return frozenset(out)
