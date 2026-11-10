"""Signed-in endpoints: account, age check, lookup sessions, TrolleyUp export, billing."""

from __future__ import annotations

import logging
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import accounts, payfast, trolleyup
from ..auth import AuthError, Identity, make_verifier
from ..db import AppUser, Listing, Price, Product, Subscription, utcnow
from ..money import from_cents
from ..queries import ProductPrices, latest_prices
from . import schemas as S

log = logging.getLogger("pricepulse.api")


def register_account_routes(app: FastAPI, ctx) -> None:  # noqa: ANN001, C901
    settings = ctx.settings
    verifier = make_verifier(settings)
    v1 = "/api/v1"

    def identity(authorization: str = Header(default="")) -> Identity:
        if not authorization.lower().startswith("bearer "):
            raise HTTPException(401, {"code": "sign_in", "message": "Please sign in."})
        try:
            return verifier.verify(authorization[7:].strip())
        except AuthError as exc:
            raise HTTPException(401, {"code": "sign_in", "message": str(exc)}) from exc

    def user_dep(who: Identity = Depends(identity), s: Session = Depends(ctx.db)) -> AppUser:  # noqa: B008
        user = accounts.get_or_create_user(s, who.uid)
        s.commit()
        return user

    def fail(exc: accounts.RuleError) -> HTTPException:
        return HTTPException(exc.status, {"code": exc.code, "message": exc.message})

    def me_out(s: Session, user: AppUser) -> S.MeOut:
        now = utcnow()
        today = accounts.sa_today(now)
        paid = accounts.is_paid(s, user, now)
        sub = s.scalar(select(Subscription).where(Subscription.uid == user.uid))
        sess = accounts.current_session(s, user.uid)
        return S.MeOut(
            id_checked=bool(user.id_fingerprint),
            adult=accounts.is_adult(user, today),
            plan="paid" if paid else "free",
            paid_until=sub.paid_until if sub and paid else None,
            subscription_status=sub.status if sub else "none",
            sessions_used_today=accounts.sessions_today(s, user.uid, today),
            sessions_per_day=None if paid else settings.free_sessions_per_day,
            session=S.SessionMeter(
                active=sess is not None,
                items_used=len(sess.items) if sess else 0,
                items_max=settings.session_items_max,
            ),
            price_per_month=from_cents(settings.price_cents),
            billing_ready=payfast.configured(settings),
        )

    def session_out(s: Session, user: AppUser, region: str) -> S.SessionOut:
        sess = accounts.current_session(s, user.uid)
        rows = list(sess.items) if sess else []
        adult = accounts.is_adult(user, accounts.sa_today())
        ids = [r.product_id for r in rows]
        grouped = latest_prices(s, region, product_ids=ids) if ids else {}
        items = []
        for r in sorted(rows, key=lambda r: r.added_at, reverse=True):
            product = s.get(Product, r.product_id)
            if product is None or (product.category == accounts.LIQUOR and not adult):
                continue
            pp = grouped.get(r.product_id) or ProductPrices(product=product)
            items.append(
                S.SessionItemOut(quantity=r.quantity, on_list=r.on_list, product=ctx.product_out(pp))
            )
        qty = {i.product.id: i.quantity for i in items if i.on_list}
        prices = [sp for pp in grouped.values() for sp in pp.prices]
        return S.SessionOut(
            region=region,
            me=me_out(s, user),
            scraped_at=max((sp.scraped_at for sp in prices), default=None),
            sample=bool(prices) and all(sp.is_sample for sp in prices),
            items=items,
            basket=ctx.basket_for(s, region, qty) if qty else None,
        )

    # --- Account ----------------------------------------------------------------

    @app.get(v1 + "/me", response_model=S.MeOut, tags=["account"])
    def me(user: AppUser = Depends(user_dep), s: Session = Depends(ctx.db)):  # noqa: B008
        return me_out(s, s.merge(user))

    @app.post(v1 + "/me/verify-id", response_model=S.MeOut, tags=["account"])
    def verify_id(
        body: S.VerifyIdIn, user: AppUser = Depends(user_dep), s: Session = Depends(ctx.db)
    ):  # noqa: B008
        """Confirm age with a South African ID number. The number is checked and thrown away."""
        if not body.accept_terms:
            raise HTTPException(
                422, {"code": "terms", "message": "Please agree to the Terms and Privacy Policy."}
            )
        user = s.merge(user)
        try:
            accounts.verify_id(s, user, body.id_number, settings)
        except accounts.RuleError as exc:
            s.rollback()
            raise fail(exc) from None
        s.commit()
        return me_out(s, user)

    @app.delete(v1 + "/me", tags=["account"])
    def delete_me(user: AppUser = Depends(user_dep), s: Session = Depends(ctx.db)):  # noqa: B008
        """Delete what PricePulse holds about you. Your TrolleyUp sign-in is not touched."""
        user = s.merge(user)
        sub = s.scalar(select(Subscription).where(Subscription.uid == user.uid))
        if sub and sub.status == "active":
            raise HTTPException(
                409,
                {"code": "cancel_first", "message": "Please cancel your monthly plan first, then delete."},
            )
        accounts.delete_user(s, user)
        s.commit()
        return {"deleted": True}

    # --- Catalogue: names only, no prices -----------------------------------------

    @app.get(v1 + "/catalogue", response_model=S.CatalogueOut, tags=["lookup"])
    def catalogue(
        region: str = Query("paarl"),
        q: str | None = Query(None, max_length=80),
        category: str | None = Query(None, max_length=40),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        user: AppUser = Depends(user_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        """Search products. Prices are shown only after an item is looked up in a session."""
        ctx.check_region(region)
        adult = accounts.is_adult(user, accounts.sa_today())
        stores = (
            select(Listing.product_id, func.count(func.distinct(Listing.store_id)).label("n"))
            .join(Price, Price.listing_id == Listing.id)
            .where(Price.region == region)
            .group_by(Listing.product_id)
            .subquery()
        )
        stmt = select(Product, stores.c.n).join(stores, stores.c.product_id == Product.id)
        if not adult:
            stmt = stmt.where(Product.category != accounts.LIQUOR)
        if category:
            stmt = stmt.where(Product.category == category)
        for word in (q or "").lower().split()[:6]:
            like = f"%{word}%"
            stmt = stmt.where(
                or_(
                    func.lower(Product.name).like(like),
                    func.lower(Product.brand).like(like),
                    func.lower(Product.size_text).like(like),
                )
            )
        rows = s.execute(stmt.order_by(Product.category, func.lower(Product.name))).all()
        sess = accounts.current_session(s, user.uid)
        have = {i.product_id for i in sess.items} if sess else set()
        page = rows[offset : offset + limit]
        note = (
            ""
            if rows
            else (
                "No products match. Try a shorter search."
                if q or category
                else "No prices for this town yet."
            )
        )
        return S.CatalogueOut(
            region=region,
            total=len(rows),
            limit=limit,
            offset=offset,
            note=note,
            items=[
                S.CatalogueItem(
                    id=p.id,
                    name=p.name,
                    brand=p.brand,
                    size=p.size_text,
                    category=p.category,
                    store_count=n,
                    in_session=p.id in have,
                )
                for p, n in page
            ],
        )

    # --- Lookup session -----------------------------------------------------------

    @app.get(v1 + "/session", response_model=S.SessionOut, tags=["lookup"])
    def get_session(
        region: str = Query("paarl"), user: AppUser = Depends(user_dep), s: Session = Depends(ctx.db)
    ):  # noqa: B008
        ctx.check_region(region)
        return session_out(s, s.merge(user), region)

    @app.post(v1 + "/session/items", response_model=S.SessionOut, tags=["lookup"])
    def add_item(
        body: S.AddItemIn, user: AppUser = Depends(user_dep), s: Session = Depends(ctx.db)
    ):  # noqa: B008
        """Look up one product's prices. Starts a session if none is open."""
        ctx.check_region(body.region)
        user = s.merge(user)
        try:
            accounts.add_item(s, user, body.product_id, settings)
        except accounts.RuleError as exc:
            s.rollback()
            raise fail(exc) from None
        s.commit()
        return session_out(s, user, body.region)

    @app.patch(v1 + "/session/items/{product_id}", response_model=S.SessionOut, tags=["lookup"])
    def patch_item(
        product_id: int, body: S.UpdateItemIn, user: AppUser = Depends(user_dep), s: Session = Depends(ctx.db)
    ):  # noqa: B008
        ctx.check_region(body.region)
        user = s.merge(user)
        try:
            accounts.update_item(s, user, product_id, body.quantity, body.on_list)
        except accounts.RuleError as exc:
            raise fail(exc) from None
        s.commit()
        return session_out(s, user, body.region)

    @app.post(v1 + "/session/clear", response_model=S.SessionOut, tags=["lookup"])
    def clear(
        body: S.RegionIn, user: AppUser = Depends(user_dep), s: Session = Depends(ctx.db)
    ):  # noqa: B008
        """End the session. The next lookup starts a new one (3 a day on the free plan)."""
        ctx.check_region(body.region)
        user = s.merge(user)
        accounts.clear_session(s, user)
        s.commit()
        return session_out(s, user, body.region)

    def in_session(s: Session, user: AppUser, on_list_only: bool = False) -> dict[int, int]:
        sess = accounts.current_session(s, user.uid)
        adult = accounts.is_adult(user, accounts.sa_today())
        out = {}
        for i in sess.items if sess else []:
            product = s.get(Product, i.product_id)
            if product and (adult or product.category != accounts.LIQUOR) and (i.on_list or not on_list_only):
                out[i.product_id] = i.quantity
        return out

    @app.get(v1 + "/session/items/{product_id}", response_model=S.ProductDetail, tags=["lookup"])
    def item_detail(
        product_id: int,
        region: str = Query("paarl"),
        user: AppUser = Depends(user_dep),
        s: Session = Depends(ctx.db),
    ):  # noqa: B008
        """All stores and 90-day price history, for an item in your session."""
        ctx.check_region(region)
        if product_id not in in_session(s, user):
            raise HTTPException(
                404, {"code": "not_in_session", "message": "Look up this item first to see its prices."}
            )
        return ctx.history_for(s, product_id, region)

    @app.get(v1 + "/session/towns", response_model=S.TownsOut, tags=["lookup"])
    def session_towns(
        region: str = Query("paarl"),
        radius_km: float = Query(50, gt=0, le=500),
        user: AppUser = Depends(user_dep),
        s: Session = Depends(ctx.db),
    ):  # noqa: B008
        """What the items on your list cost in nearby towns."""
        ctx.check_region(region)
        return ctx.towns_for(s, region, radius_km, list(in_session(s, user, on_list_only=True)))

    @app.get(v1 + "/session/export/trolleyup", tags=["lookup"])
    def export_trolleyup(
        region: str = Query("paarl"),
        mode: str = Query("split", pattern="^(split|single)$"),
        user: AppUser = Depends(user_dep),  # noqa: B008
        s: Session = Depends(ctx.db),  # noqa: B008
    ):
        """The list as a CSV file that TrolleyUp's "Import items from a CSV file" reads."""
        ctx.check_region(region)
        out = session_out(s, s.merge(user), region)
        single = (
            out.basket.cheapest_single_store.store if out.basket and out.basket.cheapest_single_store else ""
        )
        items = []
        for it in out.items:
            if not it.on_list:
                continue
            p = it.product
            row = (
                next((x for x in p.prices if x.store == single), None)
                if mode == "single" and single
                else None
            )
            store, price = (
                (row.store, row.promo_price or row.price) if row else (p.best_store or "", p.best_price)
            )
            items.append(trolleyup.ExportItem(p.name, p.size, p.category, it.quantity, store, price))
        if not items:
            raise HTTPException(
                404, {"code": "empty", "message": "Your list is empty. Look up some items first."}
            )
        today = accounts.sa_today()
        text = trolleyup.to_csv(items, f"{today.day} {today:%b}")
        name = f"pricepulse-for-trolleyup-{today.isoformat()}.csv"
        return Response(
            text,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    # --- Billing ----------------------------------------------------------------------

    @app.post(v1 + "/billing/checkout", response_model=S.CheckoutOut, tags=["billing"])
    def checkout(who: Identity = Depends(identity), s: Session = Depends(ctx.db)):  # noqa: B008
        if not payfast.configured(settings):
            raise HTTPException(
                503, {"code": "billing_off", "message": "Payments are not switched on yet. Please try later."}
            )
        user = accounts.get_or_create_user(s, who.uid)
        if not user.id_fingerprint:
            raise HTTPException(
                403, {"code": "id_required", "message": "Please confirm your age with your ID number first."}
            )
        if accounts.is_paid(s, user) and user.plan_override != "paid":
            sub = s.scalar(select(Subscription).where(Subscription.uid == user.uid))
            if sub and sub.status == "active":
                raise HTTPException(
                    409, {"code": "already_paid", "message": "You already have the monthly plan."}
                )
        out = payfast.checkout(s, user, who.email, settings)
        s.commit()
        return out

    @app.post(v1 + "/billing/cancel", response_model=S.MeOut, tags=["billing"])
    def cancel(user: AppUser = Depends(user_dep), s: Session = Depends(ctx.db)):  # noqa: B008
        """Stop the monthly plan. Access stays until the end of the paid month."""
        user = s.merge(user)
        sub = s.scalar(select(Subscription).where(Subscription.uid == user.uid))
        if not sub or sub.status != "active":
            raise HTTPException(
                404, {"code": "no_plan", "message": "You do not have an active monthly plan."}
            )
        try:
            if sub.token and not payfast.cancel_remote(settings, sub.token):
                raise RuntimeError("PayFast refused")
        except Exception as exc:  # noqa: BLE001
            log.error("Could not cancel a subscription at PayFast: %s", exc)
            raise HTTPException(
                502,
                {
                    "code": "cancel_failed",
                    "message": "We could not reach the payment company. "
                    "Please try again, or cancel from the PayFast email you received.",
                },
            ) from None
        sub.status = "cancelled"
        sub.cancelled_at = utcnow()
        s.commit()
        return me_out(s, user)

    @app.post(v1 + "/billing/payfast/itn", include_in_schema=False)
    async def payfast_itn(request: Request, s: Session = Depends(ctx.db)):  # noqa: B008
        """PayFast's server calls this. Field order matters for the signature, so read the raw body."""
        raw = (await request.body()).decode("utf-8", "replace")[:10000]
        pairs = parse_qsl(raw, keep_blank_values=True)
        outcome = payfast.handle_itn(s, pairs, settings)
        s.commit()
        log.info("payment notice", extra={"extra_fields": {"outcome": outcome}})
        ok = outcome in ("paid", "cancelled", "duplicate", "ignored")
        return PlainTextResponse("OK" if ok else "REJECTED", status_code=200 if ok else 400)
