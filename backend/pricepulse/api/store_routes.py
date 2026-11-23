"""The store owner's portal: a paying local store adds items one by one or by CSV file.

Only an account linked to a store (``admin.py store-owner <store id> <uid>``, or the Admin area)
gets in; everyone else is told "not found". An owner can see and change only their own store's
items and contact details. The plan size (``max_items``), the category, the paid-until date and
the store's name and town are set by the PricePulse owner and cannot be changed here.
"""

from __future__ import annotations

import re
from datetime import timedelta

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .. import localstores
from ..categories import CONDITIONS, SUBCATEGORIES
from ..db import AppUser, Store, StoreOwner, StoreSubmission, utcnow
from ..phones import normalise_phone, pretty_phone
from ..queries import GRACE_DAYS


class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    price: str = Field(min_length=1, max_length=20)
    promo: str = Field(default="", max_length=20)
    size: str = Field(default="", max_length=40)
    sku: str = Field(default="", max_length=40)
    barcode: str = Field(default="", max_length=20)
    department: str = Field(default="", max_length=60)
    condition: str = Field(default="", max_length=12)
    image: str = Field(default="", max_length=400)
    in_stock: bool = True


class ImportIn(BaseModel):
    csv: str = Field(max_length=2_000_000)
    filename: str = Field(default="", max_length=120)
    replace: bool = False
    dry_run: bool = False


class DetailsIn(BaseModel):
    address: str = Field(default="", max_length=160)
    phone: str = Field(default="", max_length=30)
    whatsapp: str = Field(default="", max_length=30)
    email: str = Field(default="", max_length=120)
    contact_name: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=300)


def store_status(store: Store, now=None) -> str:
    """active / grace (past the paid-until date, still shown) / lapsed (hidden) / off."""
    now = now or utcnow()
    if not store.active:
        return "off"
    if store.paid_until is None or store.paid_until > now:
        return "active"
    if store.paid_until > now - timedelta(days=GRACE_DAYS):
        return "grace"
    return "lapsed"


def register_store_routes(app: FastAPI, ctx, user_dep) -> None:  # noqa: ANN001, C901
    settings = ctx.settings
    base = "/api/v1/store"

    def owner_store(user: AppUser = Depends(user_dep), s: Session = Depends(ctx.db)) -> Store:  # noqa: B008
        link = s.get(StoreOwner, user.uid)
        store = s.get(Store, link.store_id) if link else None
        if store is None or store.kind != "local":
            raise HTTPException(404, {"code": "not_found", "message": "Not found."})
        return store

    def can_edit(store: Store) -> None:
        status = store_status(store)
        if status in ("lapsed", "off"):
            raise HTTPException(
                409,
                {
                    "code": "lapsed",
                    "message": "Your store is not active, so changes are paused. "
                    "Please contact PricePulse to renew; your items are safe.",
                },
            )

    def fail(exc: Exception, code: str = "bad_item") -> HTTPException:
        return HTTPException(422, {"code": code, "message": str(exc)})

    @app.get(base + "/me", tags=["store"])
    def me(store: Store = Depends(owner_store), s: Session = Depends(ctx.db)):  # noqa: B008
        limit = min(store.max_items or localstores.DEFAULT_MAX_ITEMS, localstores.MAX_ROWS)
        return {
            "id": store.id,
            "name": store.name,
            "town": store.town,
            "region": store.region,
            "category": store.category or "",
            "subcategories": SUBCATEGORIES.get(store.category or "", []),
            "conditions": list(CONDITIONS) if store.category in SUBCATEGORIES else [],
            "address": store.address,
            "phone": pretty_phone(normalise_phone(store.phone) or store.phone),
            "whatsapp": pretty_phone(normalise_phone(store.whatsapp) or store.whatsapp),
            "email": store.email,
            "contact_name": store.contact_name,
            "notes": store.notes,
            "max_items": limit,
            "items": localstores.active_count(s, store),
            "status": store_status(store),
            "paid_until": store.paid_until.isoformat() if store.paid_until else None,
            "grace_days": GRACE_DAYS,
            "last_submission": store.last_submission_at.isoformat() if store.last_submission_at else None,
        }

    @app.get(base + "/items", tags=["store"])
    def items(
        q: str = Query("", max_length=60),
        offset: int = Query(0, ge=0, le=100000),
        store: Store = Depends(owner_store),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        total, rows = localstores.store_items(s, store, q, offset, 100)
        return {"total": total, "items": rows}

    @app.post(base + "/items", tags=["store"])
    def add_item(
        body: ItemIn,
        store: Store = Depends(owner_store),  # noqa: B008
        user: AppUser = Depends(user_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        can_edit(store)
        try:
            lst = localstores.save_item(s, store, body.model_dump(), settings, by=user.uid)
        except localstores.ItemError as exc:
            s.rollback()
            raise fail(exc) from None
        s.commit()
        return {"id": lst.id, "active": lst.active, "items": localstores.active_count(s, store)}

    @app.post(base + "/items/{item_id}", tags=["store"])
    def change_item(
        item_id: int,
        body: ItemIn,
        store: Store = Depends(owner_store),  # noqa: B008
        user: AppUser = Depends(user_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        can_edit(store)
        try:
            lst = localstores.save_item(
                s, store, body.model_dump(), settings, listing_id=item_id, by=user.uid
            )
        except localstores.ItemError as exc:
            s.rollback()
            raise fail(exc) from None
        s.commit()
        return {"id": lst.id, "active": lst.active, "items": localstores.active_count(s, store)}

    @app.delete(base + "/items/{item_id}", tags=["store"])
    def delete_item(
        item_id: int,
        store: Store = Depends(owner_store),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        can_edit(store)
        try:
            localstores.remove_item(s, store, item_id)
        except localstores.ItemError as exc:
            s.rollback()
            raise fail(exc) from None
        s.commit()
        return {"removed": item_id, "items": localstores.active_count(s, store)}

    @app.post(base + "/import", tags=["store"])
    async def import_csv(
        body: ImportIn,
        store: Store = Depends(owner_store),  # noqa: B008
        user: AppUser = Depends(user_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        if not body.dry_run:
            can_edit(store)
        rows, problems = localstores.parse_csv(body.csv)
        if problems:
            raise HTTPException(422, {"code": "bad_file", "message": " ".join(problems)})
        if not rows:
            raise HTTPException(422, {"code": "empty", "message": "The file has no items."})

        def load():
            try:
                rep = localstores.import_for_store(
                    s,
                    store,
                    rows,
                    settings,
                    source="csv",
                    filename=body.filename,
                    replace=body.replace,
                    by=user.uid,
                    dry_run=body.dry_run,
                )
            except localstores.ImportTooBig as exc:
                s.rollback()
                raise HTTPException(422, {"code": "too_many_items", "message": str(exc)}) from None
            if not body.dry_run:
                s.commit()
            return rep

        report = await run_in_threadpool(load)
        return report.as_dict()

    @app.get(base + "/submissions", tags=["store"])
    def submissions(store: Store = Depends(owner_store), s: Session = Depends(ctx.db)):  # noqa: B008
        rows = s.scalars(
            select(StoreSubmission)
            .where(StoreSubmission.store_id == store.id)
            .order_by(StoreSubmission.id.desc())
            .limit(30)
        ).all()
        return {
            "submissions": [
                {
                    "at": r.received_at.isoformat(),
                    "source": r.source,
                    "filename": r.filename,
                    "rows": r.rows,
                    "added": r.added,
                    "updated": r.updated,
                    "removed": r.removed,
                    "invalid": r.invalid,
                }
                for r in rows
            ]
        }

    @app.post(base + "/confirm-prices", tags=["store"])
    def confirm_prices(
        store: Store = Depends(owner_store),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        can_edit(store)
        n = localstores.confirm_prices(s, store, settings)
        s.commit()
        return {"confirmed": n}

    @app.post(base + "/details", tags=["store"])
    def details(
        body: DetailsIn,
        store: Store = Depends(owner_store),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        """What shoppers see of the store (address, phone, WhatsApp). Not the name, town, plan or category."""
        for label, text in (
            ("address", body.address),
            ("notes", body.notes),
            ("contact name", body.contact_name),
        ):
            if re.search(r"https?:|www\.|@", text, re.I):
                raise HTTPException(
                    422, {"code": "bad_details", "message": f"The {label} must not contain a web address."}
                )
        phone = normalise_phone(body.phone) if body.phone.strip() else ""
        whatsapp = normalise_phone(body.whatsapp) if body.whatsapp.strip() else ""
        if body.phone.strip() and not phone:
            raise HTTPException(
                422,
                {
                    "code": "bad_phone",
                    "message": "Please type a South African phone number, like 082 123 4567.",
                },
            )
        if body.whatsapp.strip() and not whatsapp:
            raise HTTPException(
                422,
                {
                    "code": "bad_phone",
                    "message": "Please type a South African WhatsApp number, like 082 123 4567.",
                },
            )
        if body.email.strip() and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", body.email.strip()):
            raise HTTPException(
                422, {"code": "bad_email", "message": "That email address does not look right."}
            )
        store.address = re.sub(r"\s+", " ", body.address).strip()
        store.phone = phone
        store.whatsapp = whatsapp
        store.email = body.email.strip()
        store.contact_name = re.sub(r"\s+", " ", body.contact_name).strip()
        store.notes = re.sub(r"\s+", " ", body.notes).strip()
        s.commit()
        return {"saved": True}
