"""Shopping-list optimiser: cheapest single store, and cheapest split shop."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StoreTotal:
    """Cost of the list at one store."""

    store: str
    total_cents: int
    items_found: int
    full_coverage: bool
    missing: list[int] = field(default_factory=list)


@dataclass
class SplitLine:
    """One item in the split shop: where to buy it and for how much."""

    product_id: int
    store: str
    quantity: int
    unit_cents: int
    line_cents: int


@dataclass
class BasketResult:
    """Everything the List tab needs."""

    single_store: StoreTotal | None
    stores: list[StoreTotal]
    split: list[SplitLine]
    split_total_cents: int
    not_found: list[int]

    @property
    def split_saving_cents(self) -> int:
        if not self.single_store:
            return 0
        return max(0, self.single_store.total_cents - self.split_total_cents)


def optimise(quantities: dict[int, int], prices: dict[int, dict[str, int]]) -> BasketResult:
    """Work out where to shop.

    ``quantities`` maps product id to how many. ``prices`` maps product id to
    {store: effective price in cents}. Only stores that carry every item can be
    the cheapest single store. The split shop buys each item at its cheapest store.
    """
    wanted = {pid: q for pid, q in quantities.items() if q > 0}
    not_found = sorted(pid for pid in wanted if not prices.get(pid))
    found = {pid: q for pid, q in wanted.items() if prices.get(pid)}

    stores = sorted({s for pid in found for s in prices[pid]})
    totals = []
    for s in stores:
        total, have, missing = 0, 0, []
        for pid, q in found.items():
            if s in prices[pid]:
                total += prices[pid][s] * q
                have += 1
            else:
                missing.append(pid)
        totals.append(StoreTotal(s, total, have, full_coverage=not missing and bool(found), missing=missing))
    totals.sort(
        key=lambda t: (not t.full_coverage, t.total_cents if t.full_coverage else -t.items_found, t.store)
    )
    full = [t for t in totals if t.full_coverage]
    single = min(full, key=lambda t: (t.total_cents, t.store)) if full else None

    split, split_total = [], 0
    for pid, q in found.items():
        store, cents = min(prices[pid].items(), key=lambda kv: (kv[1], kv[0]))
        split.append(SplitLine(pid, store, q, cents, cents * q))
        split_total += cents * q
    split.sort(key=lambda line: (line.store, line.product_id))
    return BasketResult(single, totals, split, split_total, not_found)
