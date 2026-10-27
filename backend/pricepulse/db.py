"""Database models (SQLAlchemy 2). SQLite now; the same models work on Postgres.

Money is stored as integer cents. Times are stored as naive UTC.
Full price history is kept: one price row per listing, region and scrape slot.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import (
    Boolean,
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
    __tablename__ = "store"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)


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


MIGRATIONS: list[tuple[int, str, Callable[[Connection], None]]] = [
    (1, "initial tables", _m1_initial),
]


def make_engine(url: str) -> Engine:
    """Create an engine. SQLite gets foreign keys and WAL for safe concurrent reads."""
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
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
