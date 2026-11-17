"""The admin area: items, accounts, requests and settings for the people who run PricePulse.

Only an account with ``is_admin`` set (by hand, ``admin.py admin-role <uid> on``) gets in; everyone
else is told "not found". Nothing here shows ID numbers (they are never stored), emails (never
stored) or secrets. Accounts are shown by their sign-in ID only.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import essentials
from ..accounts import TESTER, is_paid, sa_today
from ..db import (
    AppUser,
    EssentialItem,
    ItemRequest,
    Listing,
    LookupSession,
    Price,
    Product,
    SchemaVersion,
    ScrapeRun,
    Store,
    Subscription,
)
from ..money import from_cents

MAX_ITEM_ROWS = 6000


class PlanIn(BaseModel):
    plan: str = Field(pattern="^(free|paid|tester)$")


class EssentialRowsIn(BaseModel):
    rows: list[dict] = Field(max_length=essentials.MAX_ROWS)


class EssentialRemoveIn(BaseModel):
    id: int


class RequestStatusIn(BaseModel):
    term: str = Field(min_length=1, max_length=80)
    status: str = Field(pattern="^(open|added|declined)$")


def register_admin_routes(app: FastAPI, ctx, user_dep) -> None:  # noqa: ANN001, C901
    settings = ctx.settings
    base = "/api/v1/admin"

    def admin_dep(user: AppUser = Depends(user_dep)) -> AppUser:  # noqa: B008
        if not user.is_admin:
            raise HTTPException(404, {"code": "not_found", "message": "Not found."})
        return user

    @app.get(base + "/overview", tags=["admin"])
    def overview(admin: AppUser = Depends(admin_dep), s: Session = Depends(ctx.db)):  # noqa: B008
        users = s.scalars(select(AppUser)).all()
        today = sa_today()
        runs = []
        for store in s.scalars(select(Store).order_by(Store.name)).all():
            run = s.scalar(
                select(ScrapeRun)
                .where(ScrapeRun.store == store.name, ScrapeRun.status.in_(("ok", "partial")))
                .order_by(ScrapeRun.finished_at.desc())
            )
            runs.append(
                {
                    "store": store.name,
                    "listings": s.scalar(
                        select(func.count()).select_from(Listing).where(Listing.store_id == store.id)
                    ),
                    "last_success": run.finished_at.isoformat() + "Z" if run and run.finished_at else None,
                }
            )
        multi = s.execute(
            select(Listing.product_id)
            .group_by(Listing.product_id)
            .having(func.count(func.distinct(Listing.store_id)) > 1)
        ).all()
        return {
            "accounts": len(users),
            "age_checked": sum(1 for u in users if u.id_fingerprint),
            "under_18": sum(1 for u in users if u.id_fingerprint and not u.adult),
            "paying": sum(1 for u in users if is_paid(s, u) and u.plan_override not in ("paid", TESTER)),
            "admins": sum(1 for u in users if u.is_admin),
            "sessions_today": s.scalar(
                select(func.count()).select_from(LookupSession).where(LookupSession.day == today.isoformat())
            ),
            "products": s.scalar(select(func.count()).select_from(Product)),
            "listings": s.scalar(select(func.count()).select_from(Listing)),
            "sold_by_2plus_stores": len(multi),
            "needs_review": s.scalar(
                select(func.count()).select_from(Listing).where(Listing.match_status == "needs_review")
            ),
            "open_requests": s.scalar(
                select(func.count(func.distinct(ItemRequest.key))).where(ItemRequest.status == "open")
            ),
            "stores": runs,
        }

    @app.get(base + "/items", tags=["admin"])
    def items(
        store: str = Query(""),
        category: str = Query(""),
        q: str = Query("", max_length=80),
        region: str = Query("paarl"),
        admin: AppUser = Depends(admin_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        """Every listing, one line each, with its latest price. For the on-screen list and printing."""
        ctx.check_region(region)
        latest = (
            select(Price.listing_id, func.max(Price.scraped_at).label("at"))
            .where(Price.region == region)
            .group_by(Price.listing_id)
            .subquery()
        )
        stmt = (
            select(Listing, Store.name, Product, Price)
            .join(Store, Store.id == Listing.store_id)
            .join(Product, Product.id == Listing.product_id)
            .outerjoin(latest, latest.c.listing_id == Listing.id)
            .outerjoin(
                Price,
                (Price.listing_id == Listing.id)
                & (Price.region == region)
                & (Price.scraped_at == latest.c.at),
            )
            .order_by(Store.name, Listing.name)
        )
        if store:
            stmt = stmt.where(Store.name == store)
        if category:
            stmt = stmt.where(Product.category == category)
        if q.strip():
            like = f"%{q.strip().lower()}%"
            stmt = stmt.where(func.lower(Listing.name).like(like) | func.lower(Listing.barcode).like(like))
        rows = s.execute(stmt.limit(MAX_ITEM_ROWS + 1)).all()
        truncated = len(rows) > MAX_ITEM_ROWS
        out = []
        for lst, store_name, product, price in rows[:MAX_ITEM_ROWS]:
            out.append(
                {
                    "id": lst.id,
                    "product_id": product.id,
                    "store": store_name,
                    "name": lst.name,
                    "size": lst.size_text,
                    "category": product.category,
                    "barcode": lst.barcode,
                    "price": from_cents(price.price_cents) if price else None,
                    "promo": (
                        from_cents(price.promo_cents) if price and price.promo_cents is not None else None
                    ),
                    "seen": price.scraped_at.isoformat() + "Z" if price else None,
                    "match": lst.match_status,
                    "url": lst.url,
                }
            )
        return {
            "items": out,
            "truncated": truncated,
            "stores": [n for (n,) in s.execute(select(Store.name).order_by(Store.name)).all()],
            "categories": [
                c for (c,) in s.execute(select(Product.category).distinct().order_by(Product.category)).all()
            ],
        }

    @app.get(base + "/users", tags=["admin"])
    def users(admin: AppUser = Depends(admin_dep), s: Session = Depends(ctx.db)):  # noqa: B008
        rows = s.scalars(select(AppUser).order_by(AppUser.created_at.desc()).limit(300)).all()
        out = []
        for u in rows:
            sub = s.scalar(select(Subscription).where(Subscription.uid == u.uid))
            plan = u.plan_override or ("paid" if is_paid(s, u) else "free")
            out.append(
                {
                    "uid": u.uid,
                    "created": u.created_at.isoformat() + "Z",
                    "age_checked": bool(u.id_fingerprint),
                    "adult": bool(u.adult),
                    "plan": plan,
                    "by_hand": bool(u.plan_override),
                    "paid_until": sub.paid_until.isoformat() + "Z" if sub and sub.paid_until else None,
                    "admin": bool(u.is_admin),
                    "requests": s.scalar(
                        select(func.count()).select_from(ItemRequest).where(ItemRequest.uid == u.uid)
                    ),
                }
            )
        return {"users": out, "total": s.scalar(select(func.count()).select_from(AppUser))}

    @app.post(base + "/users/{uid}/plan", tags=["admin"])
    def set_plan(
        uid: str,
        body: PlanIn,
        admin: AppUser = Depends(admin_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        """free = normal; paid = unlimited sessions and items; tester = same, for testing accounts."""
        target = s.get(AppUser, uid)
        if target is None:
            raise HTTPException(404, {"code": "not_found", "message": "No such account."})
        target.plan_override = "" if body.plan == "free" else body.plan
        s.commit()
        return {"uid": uid, "plan": body.plan}

    @app.get(base + "/requests", tags=["admin"])
    def requests(
        status: str = Query("open", pattern="^(open|added|declined)$"),
        admin: AppUser = Depends(admin_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        rows = s.execute(
            select(
                ItemRequest.key,
                func.min(ItemRequest.term),
                func.count(),
                func.min(ItemRequest.created_at),
                func.max(ItemRequest.created_at),
            )
            .where(ItemRequest.status == status)
            .group_by(ItemRequest.key)
            .order_by(func.count().desc(), func.max(ItemRequest.created_at).desc())
        ).all()
        return {
            "status": status,
            "requests": [
                {"key": k, "term": t, "count": n, "first": a.isoformat() + "Z", "last": b.isoformat() + "Z"}
                for k, t, n, a, b in rows
            ],
        }

    @app.post(base + "/requests/status", tags=["admin"])
    def set_request_status(
        body: RequestStatusIn,
        admin: AppUser = Depends(admin_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        """Mark every request for this wording added (it is in the search list) or declined."""
        key = body.term.strip().lower()
        rows = s.scalars(select(ItemRequest).where(ItemRequest.key == key)).all()
        for r in rows:
            r.status = body.status
        added = False
        if body.status == "added" and rows:
            # Approving a request puts the wording on the nightly search list straight away.
            added = (
                essentials.add_terms(s, [{"term": rows[0].term, "category": "Groceries"}], "request").added
                > 0
            )
        s.commit()
        return {"term": body.term, "status": body.status, "changed": len(rows), "search_list": added}

    @app.get(base + "/essentials", tags=["admin"])
    def essentials_list(admin: AppUser = Depends(admin_dep), s: Session = Depends(ctx.db)):  # noqa: B008
        rows = s.scalars(select(EssentialItem).order_by(EssentialItem.category, EssentialItem.term)).all()
        return {
            "categories": essentials.CATEGORIES,
            "max_rows": essentials.MAX_ROWS,
            "items": [
                {
                    "id": r.id,
                    "term": r.term,
                    "category": r.category,
                    "source": r.source,
                    "added": r.added_at.isoformat() + "Z",
                }
                for r in rows
            ],
        }

    @app.post(base + "/essentials/import", tags=["admin"])
    def essentials_import(
        body: EssentialRowsIn,
        admin: AppUser = Depends(admin_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        """Add many search words at once (the CSV file is read in the app; only clean rows get in)."""
        if not body.rows:
            raise HTTPException(422, {"code": "empty", "message": "The file has no items."})
        result = essentials.add_terms(s, body.rows, "csv")
        s.commit()
        return {
            "added": result.added,
            "existing": result.existing,
            "invalid": result.invalid[:50],
            "invalid_count": len(result.invalid),
        }

    @app.post(base + "/essentials/remove", tags=["admin"])
    def essentials_remove(
        body: EssentialRemoveIn,
        admin: AppUser = Depends(admin_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        row = s.get(EssentialItem, body.id)
        if row is None:
            raise HTTPException(404, {"code": "not_found", "message": "That item is not on the list."})
        s.delete(row)
        s.commit()
        return {"removed": body.id}

    @app.get(base + "/settings", tags=["admin"])
    def settings_view(admin: AppUser = Depends(admin_dep), s: Session = Depends(ctx.db)):  # noqa: B008
        """What the server is set to. Read only: changing a value means editing backend/.env."""
        version = s.scalar(select(func.max(SchemaVersion.version)))
        return {
            "settings": [
                ["Free lookup sessions a day", settings.free_sessions_per_day, "PP_FREE_SESSIONS"],
                ["Items per free session", settings.session_items_max, "PP_SESSION_ITEMS"],
                ["Paid plan price per month", str(from_cents(settings.price_cents)), "PP_PRICE_CENTS"],
                ["Name match: join automatically from", settings.match_auto, "PP_MATCH_AUTO"],
                ["Name match: send to review from", settings.match_review, "PP_MATCH_REVIEW"],
                ["Re-match after each upload", "on" if settings.auto_rematch else "off", "PP_AUTO_REMATCH"],
                [
                    "Price upload from the scraper machine",
                    "on" if settings.ingest_token else "off",
                    "PP_INGEST_TOKEN",
                ],
                ["Prices are marked old after (hours)", settings.stale_hours, "PP_STALE_HOURS"],
                ["Scraper alert after (hours)", settings.alert_hours, "PP_ALERT_HOURS"],
                [
                    "Payments",
                    "PayFast sandbox" if settings.payfast_sandbox else "PayFast live",
                    "PP_PAYFAST_SANDBOX",
                ],
                ["Terms version", settings.terms_version, "config.py"],
                ["Database version", version, "migrations"],
                ["Towns", ", ".join(sorted(ctx.regions.regions)), "regions.yaml"],
            ]
        }
