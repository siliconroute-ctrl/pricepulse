"""Response and request models for API v1."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_serializer


class _Model(BaseModel):
    @field_serializer("*", when_used="json", check_fields=False)
    def _utc(self, v):  # noqa: ANN001, ANN202 - serialise all datetimes as UTC "Z"
        if isinstance(v, datetime):
            return v.replace(microsecond=0).isoformat() + "Z"
        return v


class UnitPrice(_Model):
    value: float
    unit: str = Field(examples=["per 100ml"])


class PriceOut(_Model):
    store: str
    price: float
    promo_price: float | None = None
    url: str = ""
    scraped_at: datetime
    stale: bool = Field(description="True when older than the stale limit (48 hours by default)")
    unit_price: UnitPrice | None = None


class ProductOut(_Model):
    id: int
    name: str
    brand: str
    size: str
    category: str
    unit_price: UnitPrice | None = None
    prices: list[PriceOut]
    best_store: str | None
    best_price: float | None


class ProductList(_Model):
    region: str
    total: int
    limit: int
    offset: int
    scraped_at: datetime | None = Field(description="Newest price time in this region")
    sample: bool = Field(description="True when these are example prices, not real ones")
    note: str = ""
    items: list[ProductOut]


class HistoryPoint(_Model):
    date: str
    price: float


class StoreHistory(_Model):
    store: str
    points: list[HistoryPoint]


class ProductDetail(ProductOut):
    region: str
    history: list[StoreHistory]


class RegionOut(_Model):
    slug: str
    name: str
    lat: float
    lon: float
    has_data: bool
    scraped_at: datetime | None = None


class StoreStatus(_Model):
    store: str
    last_success: datetime | None
    status: str
    item_count: int
    verified: bool


class HealthOut(_Model):
    status: str = Field(description="ok, stale (no scrape in 24 hours) or empty")
    message: str
    stores: list[StoreStatus]
    regions_with_data: list[str]


class TownOut(_Model):
    region: str
    name: str
    distance_km: float
    total: float | None
    items_priced: int
    items_missing: int
    scraped_at: datetime | None = None


class TownsOut(_Model):
    region: str
    radius_km: float
    product_count: int
    note: str = ""
    towns: list[TownOut]


class BasketItemIn(BaseModel):
    product_id: int = Field(ge=1)
    quantity: int = Field(default=1, ge=1, le=99)


class BasketIn(BaseModel):
    region: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    items: list[BasketItemIn] = Field(min_length=1, max_length=200)


class StoreTotalOut(_Model):
    store: str
    total: float
    items_found: int
    full_coverage: bool
    missing_product_ids: list[int]


class SplitLineOut(_Model):
    product_id: int
    name: str
    store: str
    quantity: int
    unit_price: float
    line_total: float


class BasketOut(_Model):
    region: str
    cheapest_single_store: StoreTotalOut | None
    stores: list[StoreTotalOut]
    split: list[SplitLineOut]
    split_total: float
    split_saving: float
    not_found_product_ids: list[int]
    scraped_at: datetime | None
