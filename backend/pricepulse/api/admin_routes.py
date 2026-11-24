"""The admin area: items, accounts, requests and settings for the people who run PricePulse.

Only an account with ``is_admin`` set (by hand, ``admin.py admin-role <uid> on``) gets in; everyone
else is told "not found". Nothing here shows ID numbers (they are never stored), emails (never
stored) or secrets. Accounts are shown by their sign-in ID only.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .. import abuse, essentials, localstores
from ..accounts import TESTER, is_paid, sa_today
from ..db import (
    AbuseLock,
    AppUser,
    EssentialItem,
    ItemRequest,
    Listing,
    LookupSession,
    Price,
    Product,
    SchemaVersion,
    ScrapeRun,
    SessionItem,
    Store,
    StoreOwner,
    StoreSubmission,
    Subscription,
    utcnow,
)
from ..money import from_cents
from ..phones import normalise_phone
from ..queries import visible_clause
from .store_routes import store_status

MAX_ITEM_ROWS = 6000


class PlanIn(BaseModel):
    plan: str = Field(pattern="^(free|paid|tester)$")


class LocalStoreIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    region: str = Field(default="", max_length=40)  # the province; set from the town when one is chosen
    town_slug: str | None = Field(
        default=None, max_length=40
    )  # a town from towns.yaml (sets province and map point)
    town: str = Field(default="", max_length=60)
    address: str = Field(default="", max_length=160)
    contact_name: str = Field(default="", max_length=80)
    phone: str = Field(default="", max_length=30)
    email: str = Field(default="", max_length=120)
    notes: str = Field(default="", max_length=300)
    active: bool = True
    store_type: str = Field(default="", max_length=30)
    max_items: int = Field(default=localstores.DEFAULT_MAX_ITEMS, ge=10, le=localstores.MAX_ROWS)
    # Left out (None) = unchanged. category: the fixed category of the store's own items (Electronics);
    # whatsapp: number for the WhatsApp button; paid_until: YYYY-MM-DD ("" = no expiry).
    category: str | None = Field(default=None, max_length=40)
    whatsapp: str | None = Field(default=None, max_length=30)
    paid_until: str | None = Field(default=None, max_length=10)


class StoreDeleteIn(BaseModel):
    confirm: str = Field(max_length=80)  # the store's name, typed to confirm


class StoreApproveIn(BaseModel):
    paid_until: str = Field(max_length=10)


class StoreRejectIn(BaseModel):
    reason: str = Field(default="", max_length=200)


class StoreOwnerIn(BaseModel):
    uid: str = Field(min_length=1, max_length=128)


class StoreImportIn(BaseModel):
    csv: str = Field(max_length=2_000_000)
    filename: str = Field(default="", max_length=120)
    replace: bool = True
    dry_run: bool = False


class VisibilityIn(BaseModel):
    state: str = Field(pattern="^(auto|show|hide)$")


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
            "new_24h": s.scalar(
                select(func.count())
                .select_from(Listing)
                .where(Listing.created_at > utcnow() - timedelta(hours=24), Listing.active.is_(True))
            ),
            "locked": s.scalar(
                select(func.count(func.distinct(AbuseLock.uid))).where(
                    AbuseLock.released.is_(False), AbuseLock.locked_until > utcnow()
                )
            ),
            "sessions_today": s.scalar(
                select(func.count()).select_from(LookupSession).where(LookupSession.day == today.isoformat())
            ),
            "products": s.scalar(select(func.count()).select_from(Product)),
            "products_shown": s.scalar(select(func.count()).select_from(Product).where(visible_clause())),
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
        region: str = Query(ctx.default_region),
        shown: str = Query("", pattern="^(|yes|no)$"),
        added: str = Query("", pattern="^(|24h)$"),
        admin: AppUser = Depends(admin_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        """Every listing, one line each, with its latest price. For the on-screen list and printing.

        ``shown`` limits it to what shoppers can see ("yes") or what is hidden from them ("no").
        ``added=24h`` lists only the items that first appeared in the last 24 hours, newest first.
        """
        ctx.check_region(region)
        latest = (
            select(Price.listing_id, func.max(Price.scraped_at).label("at"))
            .where(Price.region == region)
            .group_by(Price.listing_id)
            .subquery()
        )
        stmt = (
            select(Listing, Store.name, Product, Price, visible_clause().label("vis"))
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
        if added == "24h":
            stmt = stmt.where(Listing.created_at > utcnow() - timedelta(hours=24)).order_by(None)
            stmt = stmt.order_by(Listing.created_at.desc(), Store.name, Listing.name)
        if store:
            stmt = stmt.where(Store.name == store)
        if category:
            stmt = stmt.where(Product.category == category)
        if shown == "yes":
            stmt = stmt.where(visible_clause())
        elif shown == "no":
            stmt = stmt.where(~visible_clause())
        if q.strip():
            like = f"%{q.strip().lower()}%"
            stmt = stmt.where(func.lower(Listing.name).like(like) | func.lower(Listing.barcode).like(like))
        rows = s.execute(stmt.limit(MAX_ITEM_ROWS + 1)).all()
        truncated = len(rows) > MAX_ITEM_ROWS
        out = []
        for lst, store_name, product, price, vis in rows[:MAX_ITEM_ROWS]:
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
                    "shown": bool(vis),
                    "visibility": product.visibility,
                    "hit_rank": lst.hit_rank,
                    "term": lst.term,
                    "added": lst.created_at.isoformat() + "Z" if lst.created_at else None,
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

    @app.post(base + "/products/{product_id}/visibility", tags=["admin"])
    def set_visibility(
        product_id: int,
        body: VisibilityIn,
        admin: AppUser = Depends(admin_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        """show = always shown, hide = never shown, auto = the rule (best match for a search word)."""
        product = s.get(Product, product_id)
        if product is None:
            raise HTTPException(404, {"code": "not_found", "message": "No such product."})
        product.visibility = body.state
        s.commit()
        return {"product_id": product_id, "visibility": body.state}

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
                    "locked_until": (
                        lk.locked_until.isoformat() + "Z"
                        if (lk := abuse._locked_row(s, u.uid)) is not None
                        else None
                    ),
                    "requests": s.scalar(
                        select(func.count()).select_from(ItemRequest).where(ItemRequest.uid == u.uid)
                    ),
                }
            )
        return {"users": out, "total": s.scalar(select(func.count()).select_from(AppUser))}

    @app.post(base + "/users/{uid}/unlock", tags=["admin"])
    def unlock_user(
        uid: str,
        admin: AppUser = Depends(admin_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        """Lift a pause put on an account that looked automated."""
        if s.get(AppUser, uid) is None:
            raise HTTPException(404, {"code": "not_found", "message": "No such account."})
        n = abuse.release(s, uid)
        s.commit()
        return {"uid": uid, "released": n}

    @app.post(base + "/users/{uid}/plan", tags=["admin"])
    def set_plan(
        uid: str,
        body: PlanIn,
        admin: AppUser = Depends(admin_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        """free = normal; paid = the TrolleyUp plan; tester = no limits, for testing accounts."""
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

    # --- Local (private) stores -------------------------------------------------------

    def clean_text(v: str) -> str:
        return " ".join((v or "").split())

    def store_row(st: Store, s: Session) -> dict:
        items = s.scalar(
            select(func.count())
            .select_from(Listing)
            .where(Listing.store_id == st.id, Listing.active.is_(True))
        )
        subs = s.scalar(
            select(func.count()).select_from(StoreSubmission).where(StoreSubmission.store_id == st.id)
        )
        region = ctx.regions.regions.get(st.region)
        return {
            "id": st.id,
            "store_number": st.store_number or "",
            "status_flag": st.status,
            "licence_no": st.licence_no,
            "reject_reason": st.reject_reason,
            "applied_at": st.applied_at.isoformat() + "Z" if st.applied_at else None,
            "name": st.name,
            "region": st.region,
            "town_slug": next(
                (t.slug for t in ctx.towns.values() if t.name == st.town and t.province == st.region), ""
            ),
            "region_name": region.name if region else st.region,
            "town": st.town,
            "address": st.address,
            "contact_name": st.contact_name,
            "phone": st.phone,
            "email": st.email,
            "notes": st.notes,
            "active": bool(st.active),
            "store_type": st.store_type,
            "max_items": st.max_items,
            "category": st.category,
            "whatsapp": st.whatsapp,
            "paid_until": st.paid_until.date().isoformat() if st.paid_until else "",
            "status": store_status(st),
            "owners": [
                o.uid for o in s.scalars(select(StoreOwner).where(StoreOwner.store_id == st.id)).all()
            ],
            "items": items,
            "submissions": subs,
            "last_submission": st.last_submission_at.isoformat() + "Z" if st.last_submission_at else None,
            "has_api_key": bool(st.api_key_hash),
        }

    @app.get(base + "/regions", tags=["admin"])
    def regions_list(admin: AppUser = Depends(admin_dep)):  # noqa: B008
        return {
            "regions": [{"slug": k, "name": r.name} for k, r in ctx.regions.regions.items()],
            "towns": [
                {"slug": t.slug, "name": t.name, "province": t.province}
                for t in sorted(ctx.towns.values(), key=lambda x: (x.province, x.name))
            ],
        }

    @app.get(base + "/localstores", tags=["admin"])
    def localstores_list(admin: AppUser = Depends(admin_dep), s: Session = Depends(ctx.db)):  # noqa: B008
        rows = s.scalars(select(Store).where(Store.kind == "local").order_by(Store.name)).all()
        return {
            "stores": [store_row(r, s) for r in rows],
            "max_rows": localstores.MAX_ROWS,
            "store_types": [t for t in localstores.STORE_TYPES if t],
        }

    def validate_store(body: LocalStoreIn, s: Session, current: Store | None = None) -> dict:
        data = {k: clean_text(v) if isinstance(v, str) else v for k, v in body.model_dump().items()}
        slug = data.pop("town_slug", None)
        if slug:
            t = ctx.towns.get(slug)
            if t is None:
                raise HTTPException(422, {"code": "bad_town", "message": "Choose a town from the list."})
            data.update(region=t.province, town=t.name, lat=t.lat, lon=t.lon)
        if data["region"] not in ctx.regions.regions:
            raise HTTPException(422, {"code": "bad_region", "message": "Choose a town from the list."})
        if data["store_type"] not in localstores.STORE_TYPES:
            raise HTTPException(422, {"code": "bad_type", "message": "Choose one of the listed store types."})
        if "@" in data["email"] and "." not in data["email"].split("@")[-1]:
            raise HTTPException(
                422, {"code": "bad_email", "message": "That email address does not look right."}
            )
        for key in ("category", "whatsapp", "paid_until"):
            if data.get(key) is None:
                data.pop(key, None)  # not sent: leave the stored value alone
        if "category" in data and data["category"]:
            try:
                data["category"] = localstores.store_category(data["category"])
            except ValueError as exc:
                raise HTTPException(422, {"code": "bad_category", "message": str(exc)}) from None
        if data.get("whatsapp"):
            data["whatsapp"] = normalise_phone(data["whatsapp"])
            if not data["whatsapp"]:
                raise HTTPException(
                    422, {"code": "bad_phone", "message": "Please type a South African WhatsApp number."}
                )
        if "paid_until" in data:
            text_date = data["paid_until"]
            if text_date:
                try:
                    data["paid_until"] = datetime.strptime(text_date, "%Y-%m-%d") + timedelta(
                        hours=23, minutes=59
                    )
                except ValueError:
                    raise HTTPException(
                        422, {"code": "bad_date", "message": "Type the paid-until date like 2026-12-31."}
                    ) from None
            else:
                data["paid_until"] = None
        clash = s.scalar(select(Store).where(func.lower(Store.name) == data["name"].lower()))
        if clash is not None and (current is None or clash.id != current.id):
            raise HTTPException(
                422, {"code": "name_taken", "message": "A store with that name already exists."}
            )
        return data

    @app.post(base + "/localstores", tags=["admin"])
    def localstore_create(
        body: LocalStoreIn,
        admin: AppUser = Depends(admin_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        data = validate_store(body, s)
        st = Store(kind="local", **data)
        s.add(st)
        s.flush()
        localstores.ensure_store_number(s, st)
        s.commit()
        return store_row(st, s)

    @app.post(base + "/localstores/{store_id}/delete", tags=["admin"])
    def localstore_delete(
        store_id: int,
        body: StoreDeleteIn,
        admin: AppUser = Depends(admin_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        """Delete a local store with all its items, prices, photos and owner link. Cannot be undone (the
        backup from `pp deploy` is the safety net). The store's name must be typed, and a store that is
        paid up (a paid-until date in the future) must have that date cleared first."""
        st = s.get(Store, store_id)
        if st is None or st.kind != "local":
            raise HTTPException(404, {"code": "not_found", "message": "No such local store."})
        if clean_text(body.confirm).lower() != st.name.lower():
            raise HTTPException(
                422, {"code": "confirm", "message": "Type the store's name exactly to confirm."}
            )
        if st.paid_until is not None and st.paid_until > utcnow():
            raise HTTPException(
                409,
                {
                    "code": "paid",
                    "message": "This store is paid up. Clear its paid-until date first (Edit store).",
                },
            )
        listing_ids = select(Listing.id).where(Listing.store_id == st.id)
        product_ids = [
            pid for (pid,) in s.execute(select(Listing.product_id).where(Listing.store_id == st.id)) if pid
        ]
        s.execute(delete(Price).where(Price.listing_id.in_(listing_ids)))
        s.execute(delete(Listing).where(Listing.store_id == st.id))
        # products that no other store sells disappear too (with any looked-up copies of them)
        for pid in set(product_ids):
            if s.scalar(select(func.count()).select_from(Listing).where(Listing.product_id == pid)) == 0:
                s.execute(delete(SessionItem).where(SessionItem.product_id == pid))
                prod = s.get(Product, pid)
                if prod is not None:
                    s.delete(prod)
        s.execute(delete(StoreSubmission).where(StoreSubmission.store_id == st.id))
        s.execute(delete(StoreOwner).where(StoreOwner.store_id == st.id))
        name = st.name
        s.delete(st)
        s.commit()
        shutil.rmtree(Path(settings.data_dir) / "uploads" / "stores" / str(store_id), ignore_errors=True)
        return {"deleted": name}

    @app.post(base + "/localstores/{store_id}/approve", tags=["admin"])
    def localstore_approve(
        store_id: int,
        body: StoreApproveIn,
        admin: AppUser = Depends(admin_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        """Switch a pending store on for shoppers and set the date it is paid until."""
        st = s.get(Store, store_id)
        if st is None or st.kind != "local":
            raise HTTPException(404, {"code": "not_found", "message": "No such local store."})
        try:
            until = datetime.strptime(body.paid_until, "%Y-%m-%d") + timedelta(hours=23, minutes=59)
        except ValueError:
            raise HTTPException(
                422, {"code": "bad_date", "message": "Type the paid-until date like 2027-01-31."}
            ) from None
        st.status, st.active, st.paid_until, st.reject_reason = "approved", True, until, ""
        s.commit()
        return store_row(st, s)

    @app.post(base + "/localstores/{store_id}/reject", tags=["admin"])
    def localstore_reject(
        store_id: int,
        body: StoreRejectIn,
        admin: AppUser = Depends(admin_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        st = s.get(Store, store_id)
        if st is None or st.kind != "local":
            raise HTTPException(404, {"code": "not_found", "message": "No such local store."})
        st.status, st.active, st.reject_reason = "rejected", False, clean_text(body.reason)[:200]
        s.commit()
        return store_row(st, s)

    @app.post(base + "/localstores/{store_id}/owner", tags=["admin"])
    def localstore_owner(
        store_id: int,
        body: StoreOwnerIn,
        admin: AppUser = Depends(admin_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        """Let a signed-in person (by their sign-in ID) run this store's portal."""
        st = s.get(Store, store_id)
        if st is None or st.kind != "local":
            raise HTTPException(404, {"code": "not_found", "message": "No such local store."})
        if s.get(AppUser, body.uid) is None:
            raise HTTPException(
                422,
                {
                    "code": "no_such_user",
                    "message": "No account has that sign-in ID. Ask them to sign in once first.",
                },
            )
        link = s.get(StoreOwner, body.uid)
        if link is None:
            s.add(StoreOwner(uid=body.uid, store_id=st.id))
        else:
            link.store_id = st.id
        s.commit()
        return store_row(st, s)

    @app.delete(base + "/localstores/{store_id}/owner/{uid}", tags=["admin"])
    def localstore_owner_remove(
        store_id: int,
        uid: str,
        admin: AppUser = Depends(admin_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        st = s.get(Store, store_id)
        link = s.get(StoreOwner, uid)
        if st is None or link is None or link.store_id != st.id:
            raise HTTPException(404, {"code": "not_found", "message": "That person does not run this store."})
        s.delete(link)
        s.commit()
        return store_row(st, s)

    @app.post(base + "/localstores/{store_id}", tags=["admin"])
    def localstore_update(
        store_id: int,
        body: LocalStoreIn,
        admin: AppUser = Depends(admin_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        st = s.get(Store, store_id)
        if st is None or st.kind != "local":
            raise HTTPException(404, {"code": "not_found", "message": "No such local store."})
        old_region = st.region
        for k, v in validate_store(body, s, st).items():
            setattr(st, k, v)
        if st.region != old_region:  # the store moved province: its prices follow it
            lids = select(Listing.id).where(Listing.store_id == st.id)
            s.execute(update(Price).where(Price.listing_id.in_(lids)).values(region=st.region))
        s.commit()
        return store_row(st, s)

    @app.post(base + "/localstores/{store_id}/import", tags=["admin"])
    def localstore_import(
        store_id: int,
        body: StoreImportIn,
        admin: AppUser = Depends(admin_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        """Load a store's weekly CSV. ``dry_run`` shows what would happen and keeps nothing."""
        st = s.get(Store, store_id)
        if st is None or st.kind != "local":
            raise HTTPException(404, {"code": "not_found", "message": "No such local store."})
        rows, problems = localstores.parse_csv(body.csv)
        if problems:
            raise HTTPException(422, {"code": "bad_file", "message": " ".join(problems)})
        if not rows:
            raise HTTPException(422, {"code": "empty", "message": "The file has no items."})
        try:
            report = localstores.import_for_store(
                s,
                st,
                rows,
                settings,
                filename=body.filename,
                replace=body.replace,
                by=admin.uid,
                dry_run=body.dry_run,
            )
        except localstores.ImportTooBig as exc:
            raise HTTPException(422, {"code": "too_many_items", "message": str(exc)}) from exc
        if not body.dry_run:
            s.commit()
        return report.as_dict()

    @app.get(base + "/localstores/{store_id}/submissions", tags=["admin"])
    def localstore_submissions(
        store_id: int,
        admin: AppUser = Depends(admin_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        rows = s.scalars(
            select(StoreSubmission)
            .where(StoreSubmission.store_id == store_id)
            .order_by(StoreSubmission.id.desc())
            .limit(30)
        ).all()
        return {
            "submissions": [
                {
                    "id": r.id,
                    "received": r.received_at.isoformat() + "Z",
                    "source": r.source,
                    "filename": r.filename,
                    "rows": r.rows,
                    "added": r.added,
                    "updated": r.updated,
                    "removed": r.removed,
                    "invalid": r.invalid,
                    "replace": r.replace,
                }
                for r in rows
            ]
        }

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
                ["Lookup sessions a day (everyone)", settings.free_sessions_per_day, "PP_FREE_SESSIONS"],
                ["Items per session", settings.session_items_max, "PP_SESSION_ITEMS"],
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
