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
    stale: bool = Field(
        description="True when older than the stale limit (48 hours; 8 days for local stores)"
    )
    local: bool = Field(default=False, description="True for a local (private) store")
    image: str = Field(default="", description="Picture link of this store's item, empty when none")
    store_id: int = 0
    town: str = ""  # local stores only
    address: str = ""
    phone: str = ""
    whatsapp: str = ""
    condition: str = ""  # New / Refurbished (local stores)
    sub: str = ""  # sub-category (local stores)
    unit_price: UnitPrice | None = None


class ProductOut(_Model):
    id: int
    name: str
    brand: str
    size: str
    category: str
    image: str = Field(default="", description="First picture found among the stores' items")
    new: bool = False  # found by a shopper's new-item search in the last two weeks
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
    region: str  # the province
    town: str = ""
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


# --- Accounts, sessions and billing -------------------------------------------------


class SessionMeter(_Model):
    active: bool
    items_used: int
    items_max: int


class MeOut(_Model):
    id_checked: bool = Field(description="False until the person has confirmed their age with an ID number")
    adult: bool
    is_admin: bool = False
    store_owner: bool = False  # runs a local store (the "My store" menu item)
    plan: str = Field(description="free, or paid = the TrolleyUp plan")
    paid_until: datetime | None = None
    subscription_status: str = Field(description="none, pending, active or cancelled")
    sessions_used_today: int
    sessions_per_day: int | None = Field(description="Daily limit for everyone; null only for a tester")
    session: SessionMeter
    price_per_month: float
    billing_ready: bool = Field(description="False until the payment company is set up on the server")


class VerifyIdIn(BaseModel):
    id_number: str = Field(min_length=1, max_length=40)
    accept_terms: bool


class CatalogueItem(_Model):
    id: int
    name: str
    brand: str
    size: str
    category: str
    store_count: int
    in_session: bool
    image: str = ""
    special: bool = False
    local: bool = False
    new: bool = False  # found by a shopper's new-item search in the last two weeks
    sub: str = ""  # sub-category at a local store (Electronics: Laptops, Desktops, ...)
    condition: str = ""  # New / Refurbished at a local store
    store_id: int = 0  # the local store this row's sub-category and condition come from
    store_name: str = ""
    price: float | None = None  # a local store's price shows openly (no lookup needed); chains: null
    promo: float | None = None
    detail: str = ""  # the local store's spec text for the item


class SubCount(_Model):
    name: str
    count: int


class CatalogueOut(_Model):
    region: str
    total: int
    limit: int
    offset: int
    note: str = ""
    stores: int = 0  # how many stores have prices in this town (for the front screen)
    updated: datetime | None = None  # when the newest price in this town was collected
    items: list[CatalogueItem]
    subs: list[SubCount] = []  # sub-categories with counts (for a category that has them, or one store)


class StoreOut(_Model):
    id: int
    name: str
    town: str
    address: str
    phone: str
    whatsapp: str
    store_type: str
    category: str
    items: int
    subs: list[SubCount]
    hours: dict = {}  # open_status(): state, text, today, week, note
    photos: list[str] = []
    directions: str = ""


class SessionItemOut(_Model):
    quantity: int
    on_list: bool
    product: ProductOut


class SessionOut(_Model):
    region: str
    me: MeOut
    scraped_at: datetime | None
    sample: bool
    items: list[SessionItemOut]
    basket: BasketOut | None = Field(description="Where to shop for the items on the list")


class AddItemIn(BaseModel):
    product_id: int = Field(ge=1)
    region: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")


class UpdateItemIn(BaseModel):
    region: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    quantity: int | None = Field(default=None, ge=1, le=99)
    on_list: bool | None = None


class RegionIn(BaseModel):
    region: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")


class CheckoutOut(_Model):
    action_url: str
    fields: dict[str, str]


class RequestIn(_Model):
    term: str = Field(max_length=200, description="The item the person could not find")


class RequestOut(_Model):
    id: int
    term: str
    status: str


class RequestsOut(_Model):
    requests: list[RequestOut]


class SearchIn(_Model):
    term: str = Field(max_length=200, description="The item to look for in the stores")


class SearchOut(_Model):
    id: int
    term: str
    category: str
    status: str  # pending / running / done / failed
    found: int = 0
    note: str = ""


class SearchesOut(_Model):
    searches: list[SearchOut]
