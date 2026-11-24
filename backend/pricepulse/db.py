"""Database models (SQLAlchemy 2). SQLite now; the same models work on Postgres.

Money is stored as integer cents. Times are stored as naive UTC.
Full price history is kept: one price row per listing, region and scrape slot.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    """Current UTC time without tzinfo (how times are stored)."""
    return datetime.now(UTC).replace(tzinfo=None)


def slot_for(when: datetime, slot_hours: int = 6) -> str:
    """Scrape slot label, e.g. "2026-10-27T06". Re-runs in one slot overwrite."""
    hour = (when.hour // slot_hours) * slot_hours
    return f"{when:%Y-%m-%d}T{hour:02d}"


class Base(DeclarativeBase):
    pass


class Store(Base):
    """A store. ``kind`` is "chain" (scraped national retailers) or "local" (a private store whose
    owner sends its own items and prices; it belongs to one region and is only seen there)."""

    __tablename__ = "store"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    kind: Mapped[str] = mapped_column(String(8), default="chain", server_default="chain")
    region: Mapped[str] = mapped_column(String(40), default="", server_default="")  # local: region slug
    town: Mapped[str] = mapped_column(String(60), default="", server_default="")
    address: Mapped[str] = mapped_column(String(160), default="", server_default="")
    contact_name: Mapped[str] = mapped_column(String(80), default="", server_default="")
    phone: Mapped[str] = mapped_column(String(30), default="", server_default="")
    email: Mapped[str] = mapped_column(String(120), default="", server_default="")
    notes: Mapped[str] = mapped_column(String(300), default="", server_default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, nullable=True)
    last_submission_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    api_key_hash: Mapped[str] = mapped_column(
        String(64), default="", server_default=""
    )  # sha256 of the API key
    max_items: Mapped[int] = mapped_column(
        Integer, default=1000, server_default="1000"
    )  # most items a local store may list in one file
    store_type: Mapped[str] = mapped_column(
        String(30), default="", server_default=""
    )  # Supermarket, Butchery, Stationery, IT & Electronics, Other ("" = chain)
    paid_until: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )  # a paying local store is hidden from shoppers GRACE_DAYS after this date (None = no expiry)
    category: Mapped[str] = mapped_column(
        String(40), default="", server_default=""
    )  # the category of items typed in by the owner (e.g. Electronics)
    whatsapp: Mapped[str] = mapped_column(
        String(30), default="", server_default=""
    )  # number for the WhatsApp button (blank = use phone)
    hours: Mapped[str] = mapped_column(
        Text, default="", server_default=""
    )  # opening hours as JSON: {"mon": ["08:00", "17:00"], "sun": null}; "" = not given
    hours_note: Mapped[str] = mapped_column(String(200), default="", server_default="")
    photo1: Mapped[str] = mapped_column(String(64), default="", server_default="")  # shop photo file name
    photo2: Mapped[str] = mapped_column(String(64), default="", server_default="")
    store_number: Mapped[str | None] = mapped_column(
        String(13), unique=True, nullable=True
    )  # the 13-digit Store ID (12 digits + check digit) used for every reference to a local store
    status: Mapped[str] = mapped_column(
        String(10), default="approved", server_default="approved"
    )  # pending / approved / rejected (self sign-up stores wait for approval)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)  # the store's town, for the 100 km rule
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    licence_no: Mapped[str] = mapped_column(String(40), default="", server_default="")  # liquor licence
    reject_reason: Mapped[str] = mapped_column(String(200), default="", server_default="")
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    mode: Mapped[str] = mapped_column(
        String(8), default="physical", server_default="physical"
    )  # physical, online or both: online-only stores show their town but no address or opening hours


class AbuseLock(Base):
    """An account paused because it looked automated (bots and scripts are not allowed on any plan)."""

    __tablename__ = "abuse_lock"
    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(128), index=True)
    reason: Mapped[str] = mapped_column(String(120), default="")
    locked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    locked_until: Mapped[datetime] = mapped_column(DateTime)
    released: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class Product(Base):
    """One canonical product, e.g. "Clover Full Cream Milk 2L"."""

    __tablename__ = "product"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    brand: Mapped[str] = mapped_column(String(80), default="")
    brand_key: Mapped[str] = mapped_column(String(80), default="")  # normalised, for matching
    size_text: Mapped[str] = mapped_column(String(40), default="")
    size_key: Mapped[str] = mapped_column(String(40), default="", index=True)
    unit: Mapped[str] = mapped_column(String(8), default="")  # g, ml, each
    qty: Mapped[float] = mapped_column(Float, default=0)  # per item, base units
    pack: Mapped[int] = mapped_column(Integer, default=1)
    by_weight: Mapped[bool] = mapped_column(Boolean, default=False)
    category: Mapped[str] = mapped_column(String(40), default="Groceries", index=True)
    norm_name: Mapped[str] = mapped_column(String(200), default="")
    is_generic: Mapped[bool] = mapped_column(Boolean, default=False)
    visibility: Mapped[str] = mapped_column(
        String(6), default="auto", server_default="auto"
    )  # auto / show / hide (admin override)
    via_search: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )  # created by a shopper's new-item search (shown with a "New" tag for a while)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    listings: Mapped[list[Listing]] = relationship(
        back_populates="product", foreign_keys="Listing.product_id"
    )


class Listing(Base):
    """A store's own entry for a product: its name, size, URL and category."""

    __tablename__ = "listing"
    __table_args__ = (UniqueConstraint("store_id", "ident", name="uq_listing_store_ident"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("store.id"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("product.id"), index=True)
    ident: Mapped[str] = mapped_column(String(400))  # URL, or name|size when no URL
    name: Mapped[str] = mapped_column(String(200))
    size_text: Mapped[str] = mapped_column(String(40), default="")
    url: Mapped[str] = mapped_column(String(400), default="")
    category: Mapped[str] = mapped_column(String(40), default="")
    barcode: Mapped[str] = mapped_column(String(20), default="", index=True)  # digits only, "" = unknown
    hit_rank: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # 1 = best match for `term`; None = not known
    term: Mapped[str] = mapped_column(
        String(80), default="", server_default=""
    )  # the search word that last found it
    sku: Mapped[str] = mapped_column(String(40), default="", server_default="")  # the store's own item code
    department: Mapped[str] = mapped_column(
        String(60), default="", server_default=""
    )  # e.g. Butchery; for Electronics the sub-category (Laptops, Desktops, ...)
    condition: Mapped[str] = mapped_column(
        String(12), default="", server_default=""
    )  # New / Refurbished ("" = not stated)
    description: Mapped[str] = mapped_column(
        String(500), default="", server_default=""
    )  # a longer description for the item's View page
    image_url: Mapped[str] = mapped_column(
        String(400), default="", server_default=""
    )  # https picture link, or our own /api/v1/img/... path
    source: Mapped[str] = mapped_column(
        String(8), default="scrape", server_default="scrape"
    )  # scrape/csv/api
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1"
    )  # False = not in the latest list
    match_status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    match_score: Mapped[float] = mapped_column(Float, default=0)
    match_reason: Mapped[str] = mapped_column(String(200), default="")
    suggestion_id: Mapped[int | None] = mapped_column(ForeignKey("product.id"))
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    store: Mapped[Store] = relationship()
    product: Mapped[Product | None] = relationship(back_populates="listings", foreign_keys=[product_id])
    suggestion: Mapped[Product | None] = relationship(foreign_keys=[suggestion_id])


class Price(Base):
    """One observed price. Never deleted: this is the price history."""

    __tablename__ = "price"
    __table_args__ = (UniqueConstraint("listing_id", "region", "slot", name="uq_price_listing_region_slot"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listing.id"), index=True)
    region: Mapped[str] = mapped_column(String(40), index=True)
    price_cents: Mapped[int] = mapped_column(Integer)
    promo_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    slot: Mapped[str] = mapped_column(String(16))
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    listing: Mapped[Listing] = relationship()


class ScrapeRun(Base):
    """One store's run for one region: what was found and what went wrong."""

    __tablename__ = "scrape_run"
    id: Mapped[int] = mapped_column(primary_key=True)
    store: Mapped[str] = mapped_column(String(80), index=True)
    region: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # ok, partial, blocked, failed, sample
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    terms_total: Mapped[int] = mapped_column(Integer, default=0)
    terms_missing: Mapped[str] = mapped_column(Text, default="")  # newline separated
    error: Mapped[str] = mapped_column(Text, default="")


class AppUser(Base):
    """A signed-in person. ``uid`` is the Firebase account ID (shared with TrolleyUp).

    No name, email or ID number is stored: only the age result and a keyed
    fingerprint of the ID number (see said.py).
    """

    __tablename__ = "app_user"
    uid: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    id_fingerprint: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    id_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    adult: Mapped[bool] = mapped_column(Boolean, default=False)
    adult_on: Mapped[date | None] = mapped_column(Date, nullable=True)  # under-18s: day they turn 18
    terms_version: Mapped[int] = mapped_column(Integer, default=0)
    plan_override: Mapped[str] = mapped_column(String(10), default="")  # "paid" = granted by hand
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)  # set by hand with admin.py admin-role


class Subscription(Base):
    """The R99-a-month plan for one user."""

    __tablename__ = "subscription"
    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(ForeignKey("app_user.uid"), unique=True)
    provider: Mapped[str] = mapped_column(String(20), default="payfast")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, active, cancelled
    token: Mapped[str] = mapped_column(String(80), default="")  # the provider's subscription token
    m_payment_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Payment(Base):
    """One payment notice from the payment company. Kept for the books; no card or payer details."""

    __tablename__ = "payment"
    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(128), index=True)
    provider: Mapped[str] = mapped_column(String(20), default="payfast")
    provider_payment_id: Mapped[str] = mapped_column(String(64), unique=True)
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="")
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LookupSession(Base):
    """One price-lookup session: up to 45 items. ``day`` is the South African date it started."""

    __tablename__ = "lookup_session"
    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(ForeignKey("app_user.uid"), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    items: Mapped[list[SessionItem]] = relationship(back_populates="session", cascade="all, delete-orphan")


class SessionItem(Base):
    """A product looked up in a session. Stays counted even when taken off the list."""

    __tablename__ = "session_item"
    __table_args__ = (UniqueConstraint("session_id", "product_id", name="uq_session_product"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("lookup_session.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    on_list: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    session: Mapped[LookupSession] = relationship(back_populates="items")


class ItemRequest(Base):
    """A shopper asks for a product we do not list yet. The owner reads these and adds search terms."""

    __tablename__ = "item_request"
    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(128), index=True)
    term: Mapped[str] = mapped_column(String(80))
    key: Mapped[str] = mapped_column(String(80), index=True)  # lower-case, single spaces
    status: Mapped[str] = mapped_column(String(10), default="open")  # open / added / declined
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StoreOwner(Base):
    """Links a signed-in person to the one local store they run (set by the owner of PricePulse)."""

    __tablename__ = "store_owner"
    uid: Mapped[str] = mapped_column(String(128), primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("store.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SearchJob(Base):
    """A paid shopper asks us to look for a product that is not listed. The scraper machine picks
    these up every few minutes, searches the stores and uploads what it finds."""

    __tablename__ = "search_job"
    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(128), index=True)
    term: Mapped[str] = mapped_column(String(80))
    key: Mapped[str] = mapped_column(String(80), index=True)  # lower-case, single spaces
    category: Mapped[str] = mapped_column(String(40), default="Groceries")
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending / running / done / failed
    found: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EssentialItem(Base):
    """A search word list the scrapers look up every night (added by the owner in the admin area)."""

    __tablename__ = "essential_item"
    id: Mapped[int] = mapped_column(primary_key=True)
    term: Mapped[str] = mapped_column(String(80))
    key: Mapped[str] = mapped_column(String(80), unique=True, index=True)  # lower-case
    category: Mapped[str] = mapped_column(String(40), default="Groceries")
    source: Mapped[str] = mapped_column(String(20), default="admin")  # csv / request / admin
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StoreSubmission(Base):
    """One weekly file (or API call) from a local store: what arrived and what was done with it."""

    __tablename__ = "store_submission"
    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("store.id"), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(8), default="csv")  # csv / api
    filename: Mapped[str] = mapped_column(String(120), default="")
    rows: Mapped[int] = mapped_column(Integer, default=0)
    added: Mapped[int] = mapped_column(Integer, default=0)  # new items
    updated: Mapped[int] = mapped_column(Integer, default=0)  # known items, price refreshed
    removed: Mapped[int] = mapped_column(Integer, default=0)  # left out of this file, hidden (replace mode)
    invalid: Mapped[int] = mapped_column(Integer, default=0)
    replace: Mapped[bool] = mapped_column(Boolean, default=True)
    by: Mapped[str] = mapped_column(String(128), default="")  # admin account or "api"


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    version: Mapped[int] = mapped_column(primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    description: Mapped[str] = mapped_column(String(200), default="")


# --- Migrations --------------------------------------------------------------
# Append new steps; never edit or remove old ones. Each step gets a connection
# inside a transaction. Steps must not drop data.


def _m1_initial(conn: Connection) -> None:
    Base.metadata.create_all(conn)


def _m2_accounts(conn: Connection) -> None:
    """Accounts, subscriptions, payments and lookup sessions (adds tables only)."""
    tables = [m.__table__ for m in (AppUser, Subscription, Payment, LookupSession, SessionItem)]
    Base.metadata.create_all(conn, tables=tables)


def _m3_barcode(conn: Connection) -> None:
    """Add listing.barcode (adds a column only; fresh databases already have it from step 1)."""
    cols = {c["name"] for c in inspect(conn).get_columns("listing")}
    if "barcode" not in cols:
        conn.execute(text("ALTER TABLE listing ADD COLUMN barcode VARCHAR(20) NOT NULL DEFAULT ''"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listing_barcode ON listing (barcode)"))


def _m4_requests(conn: Connection) -> None:
    """Item requests (adds one table only)."""
    Base.metadata.create_all(conn, tables=[ItemRequest.__table__])


def _m5_admin_flag(conn: Connection) -> None:
    """Add app_user.is_admin (adds a column only; fresh databases already have it)."""
    cols = {c["name"] for c in inspect(conn).get_columns("app_user")}
    if "is_admin" not in cols:
        conn.execute(text("ALTER TABLE app_user ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0"))


def _m6_essentials(conn: Connection) -> None:
    """Essential items (adds one table only)."""
    Base.metadata.create_all(conn, tables=[EssentialItem.__table__])


def _m7_visibility(conn: Connection) -> None:
    """Search-hit rank and the admin show/hide switch (adds columns only; fresh databases have them)."""
    lcols = {c["name"] for c in inspect(conn).get_columns("listing")}
    if "hit_rank" not in lcols:
        conn.execute(text("ALTER TABLE listing ADD COLUMN hit_rank INTEGER"))
    if "term" not in lcols:
        conn.execute(text("ALTER TABLE listing ADD COLUMN term VARCHAR(80) NOT NULL DEFAULT ''"))
    pcols = {c["name"] for c in inspect(conn).get_columns("product")}
    if "visibility" not in pcols:
        conn.execute(text("ALTER TABLE product ADD COLUMN visibility VARCHAR(6) NOT NULL DEFAULT 'auto'"))


def _m8_local_stores(conn: Connection) -> None:
    """Local stores: more store fields, item code / department / active on listings, a submissions log
    (adds columns and one table only; fresh databases already have them)."""
    scols = {c["name"] for c in inspect(conn).get_columns("store")}
    for name, ddl in (
        ("kind", "VARCHAR(8) NOT NULL DEFAULT 'chain'"),
        ("region", "VARCHAR(40) NOT NULL DEFAULT ''"),
        ("town", "VARCHAR(60) NOT NULL DEFAULT ''"),
        ("address", "VARCHAR(160) NOT NULL DEFAULT ''"),
        ("contact_name", "VARCHAR(80) NOT NULL DEFAULT ''"),
        ("phone", "VARCHAR(30) NOT NULL DEFAULT ''"),
        ("email", "VARCHAR(120) NOT NULL DEFAULT ''"),
        ("notes", "VARCHAR(300) NOT NULL DEFAULT ''"),
        ("active", "BOOLEAN NOT NULL DEFAULT 1"),
        ("created_at", "DATETIME"),
        ("last_submission_at", "DATETIME"),
        ("api_key_hash", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ):
        if name not in scols:
            conn.execute(text(f"ALTER TABLE store ADD COLUMN {name} {ddl}"))
    lcols = {c["name"] for c in inspect(conn).get_columns("listing")}
    for name, ddl in (
        ("sku", "VARCHAR(40) NOT NULL DEFAULT ''"),
        ("department", "VARCHAR(60) NOT NULL DEFAULT ''"),
        ("source", "VARCHAR(8) NOT NULL DEFAULT 'scrape'"),
        ("active", "BOOLEAN NOT NULL DEFAULT 1"),
    ):
        if name not in lcols:
            conn.execute(text(f"ALTER TABLE listing ADD COLUMN {name} {ddl}"))
    Base.metadata.create_all(conn, tables=[StoreSubmission.__table__])


def _m9_pictures(conn: Connection) -> None:
    """Item picture links and the store type (adds columns only; fresh databases already have them)."""
    lcols = {c["name"] for c in inspect(conn).get_columns("listing")}
    if "image_url" not in lcols:
        conn.execute(text("ALTER TABLE listing ADD COLUMN image_url VARCHAR(400) NOT NULL DEFAULT ''"))
    scols = {c["name"] for c in inspect(conn).get_columns("store")}
    if "store_type" not in scols:
        conn.execute(text("ALTER TABLE store ADD COLUMN store_type VARCHAR(30) NOT NULL DEFAULT ''"))


def _m10_store_limits(conn: Connection) -> None:
    """Per-store item limit (adds a column only)."""
    scols = {c["name"] for c in inspect(conn).get_columns("store")}
    if "max_items" not in scols:
        conn.execute(text("ALTER TABLE store ADD COLUMN max_items INTEGER NOT NULL DEFAULT 1000"))


def _m11_new_item_search(conn: Connection) -> None:
    """New-item searches: a job table and a product flag (adds one table and one column only)."""
    Base.metadata.create_all(conn, tables=[SearchJob.__table__])
    pcols = {c["name"] for c in inspect(conn).get_columns("product")}
    if "via_search" not in pcols:
        conn.execute(text("ALTER TABLE product ADD COLUMN via_search BOOLEAN NOT NULL DEFAULT 0"))


def _m12_store_portal(conn: Connection) -> None:
    """Store-owner portal: owner link table, store paid-until / category / WhatsApp, item condition
    (adds one table and columns only; fresh databases already have them)."""
    Base.metadata.create_all(conn, tables=[StoreOwner.__table__])
    scols = {c["name"] for c in inspect(conn).get_columns("store")}
    for name, ddl in (
        ("paid_until", "DATETIME"),
        ("category", "VARCHAR(40) NOT NULL DEFAULT ''"),
        ("whatsapp", "VARCHAR(30) NOT NULL DEFAULT ''"),
    ):
        if name not in scols:
            conn.execute(text(f"ALTER TABLE store ADD COLUMN {name} {ddl}"))
    lcols = {c["name"] for c in inspect(conn).get_columns("listing")}
    if "condition" not in lcols:
        conn.execute(text("ALTER TABLE listing ADD COLUMN condition VARCHAR(12) NOT NULL DEFAULT ''"))


def _m13_store_pages(conn: Connection) -> None:
    """Opening hours, shop photos and item descriptions (adds columns only)."""
    scols = {c["name"] for c in inspect(conn).get_columns("store")}
    for name, ddl in (
        ("hours", "TEXT NOT NULL DEFAULT ''"),
        ("hours_note", "VARCHAR(200) NOT NULL DEFAULT ''"),
        ("photo1", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("photo2", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ):
        if name not in scols:
            conn.execute(text(f"ALTER TABLE store ADD COLUMN {name} {ddl}"))
    lcols = {c["name"] for c in inspect(conn).get_columns("listing")}
    if "description" not in lcols:
        conn.execute(text("ALTER TABLE listing ADD COLUMN description VARCHAR(500) NOT NULL DEFAULT ''"))


def _m14_store_signup(conn: Connection) -> None:
    """Store sign-up: 13-digit Store ID, approval status, map point and liquor licence (columns only)."""
    from .storeid import make_store_id

    cols = {c["name"] for c in inspect(conn).get_columns("store")}
    for name, ddl in (
        ("store_number", "VARCHAR(13)"),
        ("status", "VARCHAR(10) NOT NULL DEFAULT 'approved'"),
        ("lat", "FLOAT"),
        ("lon", "FLOAT"),
        ("licence_no", "VARCHAR(40) NOT NULL DEFAULT ''"),
        ("reject_reason", "VARCHAR(200) NOT NULL DEFAULT ''"),
        ("applied_at", "DATETIME"),
    ):
        if name not in cols:
            conn.execute(text(f"ALTER TABLE store ADD COLUMN {name} {ddl}"))
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_store_number ON store (store_number)"))
    for (sid,) in conn.execute(text("SELECT id FROM store WHERE kind = 'local' AND store_number IS NULL")):
        conn.execute(
            text("UPDATE store SET store_number = :n WHERE id = :i"), {"n": make_store_id(), "i": sid}
        )


OLD_TOWN_REGIONS = (
    "paarl",
    "wellington",
    "franschhoek",
    "stellenbosch",
    "cape-town",
    "worcester",
    "hermanus",
)


def _m15_provinces(conn: Connection) -> None:
    """Regions become the nine provinces. The seven old Western Cape towns become 'western-cape':
    one price row is kept per listing and slot (chain prices were the same in every town), history is
    kept, other tables are only renamed. Local stores get their town's map point. Idempotent."""
    old = ", ".join(f"'{r}'" for r in OLD_TOWN_REGIONS)
    # local stores: map point from the old town before the region is renamed
    try:
        import yaml

        from .config import BACKEND_DIR

        raw = yaml.safe_load((BACKEND_DIR / "towns.yaml").read_text(encoding="utf-8")) or {}
        towns = raw.get("towns") or {}
    except Exception:  # noqa: BLE001 - a missing towns file only means no map points
        towns = {}
    for slug in OLD_TOWN_REGIONS:
        t = towns.get(slug)
        if t:
            conn.execute(
                text(
                    "UPDATE store SET lat = :lat, lon = :lon, town = CASE WHEN town = '' THEN :name "
                    "ELSE town END WHERE kind = 'local' AND region = :r AND lat IS NULL"
                ),
                {"lat": float(t["lat"]), "lon": float(t["lon"]), "name": t["name"], "r": slug},
            )
    conn.execute(text("UPDATE price SET region = 'western-cape' WHERE region = 'paarl'"))
    conn.execute(
        text(
            f"DELETE FROM price WHERE region IN ({old}) AND EXISTS (SELECT 1 FROM price p2 "
            "WHERE p2.listing_id = price.listing_id AND p2.slot = price.slot AND p2.region = 'western-cape')"
        )
    )
    conn.execute(
        text(
            f"UPDATE price SET region = 'western-cape' WHERE id IN (SELECT MIN(id) FROM price "
            f"WHERE region IN ({old}) GROUP BY listing_id, slot)"
        )
    )
    conn.execute(text(f"DELETE FROM price WHERE region IN ({old})"))
    conn.execute(text(f"UPDATE scrape_run SET region = 'western-cape' WHERE region IN ({old})"))
    conn.execute(text(f"UPDATE store SET region = 'western-cape' WHERE region IN ({old})"))


def _m16_abuse_lock(conn: Connection) -> None:
    """Accounts paused for automated use (adds a table only)."""
    conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS abuse_lock (id INTEGER PRIMARY KEY, uid VARCHAR(128) NOT NULL, "
            "reason VARCHAR(120) NOT NULL DEFAULT '', locked_at DATETIME NOT NULL, "
            "locked_until DATETIME NOT NULL, released BOOLEAN NOT NULL DEFAULT 0)"
        )
    )
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_abuse_lock_uid ON abuse_lock (uid)"))


def _m17_store_mode(conn: Connection) -> None:
    """A store is a physical shop, online only, or both (adds a column only)."""
    cols = {c["name"] for c in inspect(conn).get_columns("store")}
    if "mode" not in cols:
        conn.execute(text("ALTER TABLE store ADD COLUMN mode VARCHAR(8) NOT NULL DEFAULT 'physical'"))


MIGRATIONS: list[tuple[int, str, Callable[[Connection], None]]] = [
    (1, "initial tables", _m1_initial),
    (2, "accounts, subscriptions and lookup sessions", _m2_accounts),
    (3, "listing barcode", _m3_barcode),
    (4, "item requests", _m4_requests),
    (5, "admin flag", _m5_admin_flag),
    (6, "essential items", _m6_essentials),
    (7, "search-hit rank and product visibility", _m7_visibility),
    (8, "local stores, item codes, departments and submissions", _m8_local_stores),
    (9, "item pictures and store type", _m9_pictures),
    (10, "store item limit", _m10_store_limits),
    (11, "new-item searches", _m11_new_item_search),
    (12, "store-owner portal", _m12_store_portal),
    (13, "store pages: hours, photos, item descriptions", _m13_store_pages),
    (14, "store sign-up: store id, approval, map point", _m14_store_signup),
    (15, "regions are provinces", _m15_provinces),
    (16, "accounts paused for automated use", _m16_abuse_lock),
    (17, "store operating mode", _m17_store_mode),
]


BUSY_SECONDS = 60


def make_engine(url: str) -> Engine:
    """Create an engine. SQLite gets foreign keys and WAL for safe concurrent reads."""
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        # A writer waits up to a minute for another writer (a big price upload) instead of failing at once
        # with "database is locked" after the default 5 seconds.
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": BUSY_SECONDS})

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute(f"PRAGMA busy_timeout={BUSY_SECONDS * 1000}")
            cur.close()

        return engine
    return create_engine(url, pool_pre_ping=True)


def migrate(engine: Engine) -> list[int]:
    """Apply pending migrations. Returns the versions applied."""
    applied: list[int] = []
    with engine.begin() as conn:
        if not inspect(conn).has_table("schema_version"):
            SchemaVersion.__table__.create(conn)
        done = {row[0] for row in conn.execute(text("SELECT version FROM schema_version"))}
        for version, desc, step in MIGRATIONS:
            if version in done:
                continue
            step(conn)
            conn.execute(
                SchemaVersion.__table__.insert().values(
                    version=version, description=desc, applied_at=utcnow()
                )
            )
            applied.append(version)
    return applied


_engines: dict[str, Engine] = {}


def get_engine(url: str) -> Engine:
    """One engine per URL, migrated on first use."""
    if url not in _engines:
        engine = make_engine(url)
        migrate(engine)
        _engines[url] = engine
    return _engines[url]


def session_factory(url: str) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False)


def get_or_create_store(session: Session, name: str) -> Store:
    store = session.scalar(select(Store).where(Store.name == name))
    if store is None:
        store = Store(name=name)
        session.add(store)
        session.flush()
    return store


def history_cutoff(days: int = 90) -> datetime:
    return utcnow() - timedelta(days=days)
