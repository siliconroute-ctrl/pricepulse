"""Read-side queries shared by the API and the admin CLI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .db import Listing, Price, Product, ScrapeRun, Store
from .sizes import Size


@dataclass
class StorePrice:
    """The latest price of one product at one store in one region."""

    store: str
    price_cents: int
    promo_cents: int | None
    url: str
    scraped_at: datetime
    is_sample: bool

    @property
    def effective_cents(self) -> int:
        """What the shopper pays: the promo price when there is one."""
        return self.promo_cents if self.promo_cents is not None else self.price_cents


@dataclass
class ProductPrices:
    """A product with its latest price per store."""

    product: Product
    prices: list[StorePrice] = field(default_factory=list)

    @property
    def best(self) -> StorePrice | None:
        return min(self.prices, key=lambda p: (p.effective_cents, p.store)) if self.prices else None

    def size(self) -> Size | None:
        p = self.product
        return Size(qty=p.qty, unit=p.unit, pack=p.pack, by_weight=p.by_weight) if p.unit else None


def latest_prices(
    session: Session,
    region: str,
    product_ids: list[int] | None = None,
    q: str | None = None,
    category: str | None = None,
) -> dict[int, ProductPrices]:
    """Latest price per listing in ``region``, grouped by product.

    When one store has two listings for the same product, the cheaper one wins.
    """
    latest = (
        select(Price.listing_id, func.max(Price.scraped_at).label("at"))
        .where(Price.region == region)
        .group_by(Price.listing_id)
        .subquery()
    )
    stmt = (
        select(Price, Listing, Store, Product)
        .join(latest, (latest.c.listing_id == Price.listing_id) & (latest.c.at == Price.scraped_at))
        .join(Listing, Listing.id == Price.listing_id)
        .join(Store, Store.id == Listing.store_id)
        .join(Product, Product.id == Listing.product_id)
        .where(Price.region == region)
    )
    if product_ids:
        stmt = stmt.where(Product.id.in_(product_ids))
    if category:
        stmt = stmt.where(Product.category == category)
    if q:
        for word in q.lower().split()[:6]:
            like = f"%{word}%"
            stmt = stmt.where(
                or_(
                    func.lower(Product.name).like(like),
                    func.lower(Product.brand).like(like),
                    func.lower(Product.size_text).like(like),
                )
            )
    out: dict[int, ProductPrices] = {}
    for price, listing, store, product in session.execute(stmt):
        pp = out.setdefault(product.id, ProductPrices(product=product))
        sp = StorePrice(
            store=store.name,
            price_cents=price.price_cents,
            promo_cents=price.promo_cents,
            url=listing.url,
            scraped_at=price.scraped_at,
            is_sample=price.is_sample,
        )
        current = next((x for x in pp.prices if x.store == store.name), None)
        if current is None:
            pp.prices.append(sp)
        elif sp.effective_cents < current.effective_cents:
            pp.prices[pp.prices.index(current)] = sp
    for pp in out.values():
        pp.prices.sort(key=lambda p: (p.effective_cents, p.store))
    return out


def regions_with_data(session: Session) -> dict[str, datetime]:
    """Each region that has prices, with its newest scrape time."""
    rows = session.execute(select(Price.region, func.max(Price.scraped_at)).group_by(Price.region))
    return {r: at for r, at in rows}


def last_success_per_store(session: Session) -> dict[str, ScrapeRun]:
    """Most recent ok/partial (or sample) run per store."""
    runs = session.scalars(
        select(ScrapeRun)
        .where(ScrapeRun.status.in_(["ok", "partial", "sample"]))
        .order_by(ScrapeRun.finished_at.desc())
    ).all()
    out: dict[str, ScrapeRun] = {}
    for r in runs:
        out.setdefault(r.store, r)
    return out
