"""Admin tasks for PricePulse ZA, in plain language.

python admin.py init-db                 create or upgrade the database
python admin.py ingest data/latest_western-cape.json
python admin.py report                  match rate and review queue size
python admin.py review                  go through uncertain matches one by one
python admin.py merge 12 34             move all listings of product 34 into 12
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pricepulse.config import get_settings
from pricepulse.db import Listing, Product, Store, get_engine, session_factory
from pricepulse.ingest import ingest_file


def match_report(s: Session) -> dict:
    """Numbers behind the match report."""
    total = s.scalar(select(func.count(Listing.id))) or 0
    by_status = dict(
        s.execute(select(Listing.match_status, func.count()).group_by(Listing.match_status)).all()
    )
    products = s.scalar(select(func.count(Product.id))) or 0
    multi = s.execute(
        select(Listing.product_id)
        .group_by(Listing.product_id)
        .having(func.count(func.distinct(Listing.store_id)) > 1)
    ).all()
    matched = by_status.get("exact", 0) + by_status.get("fuzzy", 0) + by_status.get("manual", 0)
    return {
        "listings": total,
        "products": products,
        "products_at_2plus_stores": len(multi),
        "matched_listings": matched,
        "match_rate": round(100 * len(multi) / products, 1) if products else 0.0,
        "needs_review": by_status.get("needs_review", 0),
        "by_status": by_status,
    }


def purge_sample(s: Session) -> dict[str, int]:
    """Remove example (made-up) prices and whatever is left with no price at all.

    Accounts, age checks, sessions' daily counts, subscriptions and real prices stay.
    A looked-up item whose product disappears is taken out of that person's session.
    """
    from sqlalchemy import delete, update

    from pricepulse.db import Price, ScrapeRun, SessionItem

    out = {"prices": s.execute(delete(Price).where(Price.is_sample.is_(True))).rowcount}
    bare = s.scalars(select(Listing.id).where(Listing.id.not_in(select(Price.listing_id)))).all()
    if bare:
        s.execute(delete(Listing).where(Listing.id.in_(bare)))
    out["listings"] = len(bare)
    used = select(Listing.product_id).where(Listing.product_id.is_not(None))
    gone = s.scalars(select(Product.id).where(Product.id.not_in(used))).all()
    if gone:
        s.execute(update(Listing).where(Listing.suggestion_id.in_(gone)).values(suggestion_id=None))
        s.execute(delete(SessionItem).where(SessionItem.product_id.in_(gone)))
        s.execute(delete(Product).where(Product.id.in_(gone)))
    out["products"] = len(gone)
    out["runs"] = s.execute(delete(ScrapeRun).where(ScrapeRun.status == "sample")).rowcount
    return out


def merge(s: Session, keep_id: int, drop_id: int) -> int:
    """Move listings from product ``drop_id`` into ``keep_id``. Nothing is deleted."""
    if keep_id == drop_id:
        return 0
    moved = 0
    for lst in s.scalars(select(Listing).where(Listing.product_id == drop_id)):
        lst.product_id = keep_id
        lst.match_status = "manual"
        lst.suggestion_id = None
        moved += 1
    return moved


def review(s: Session, ask=input, say=print) -> None:  # noqa: ANN001
    """Walk through the review queue. y = same product, n = different, s = skip, q = quit."""
    queue = s.scalars(
        select(Listing).where(Listing.match_status == "needs_review").order_by(Listing.id)
    ).all()
    if not queue:
        say("Nothing to review. All listings are matched or clearly new.")
        return
    say(f"{len(queue)} listings need a decision.\n")
    for i, lst in enumerate(queue, 1):
        store = s.get(Store, lst.store_id).name
        say(f"[{i}/{len(queue)}] {store}: {lst.name} ({lst.size_text or 'no size'})")
        sug = s.get(Product, lst.suggestion_id) if lst.suggestion_id else None
        if sug is None:
            say(f"   Reason: {lst.match_reason or 'unclear'}. No suggestion.")
            ans = ask("   Keep as its own product? [y = yes / s = skip / q = quit] ").strip().lower()
            if ans == "q":
                break
            if ans == "y":
                lst.match_status = "new"
            continue
        say(f"   Looks like product {sug.id}: {sug.name} ({sug.size_text}) - score {lst.match_score:.0f}")
        ans = ask("   Same product? [y = yes / n = no / s = skip / q = quit] ").strip().lower()
        if ans == "q":
            break
        if ans == "y":
            own = lst.product_id
            lst.product_id = sug.id
            lst.match_status = "manual"
            lst.suggestion_id = None
            # Drop the placeholder product if nothing else uses it.
            if own and own != sug.id and not s.scalar(select(func.count()).where(Listing.product_id == own)):
                s.delete(s.get(Product, own))
            say("   Merged.")
        elif ans == "n":
            lst.match_status = "new"
            lst.suggestion_id = None
            say("   Kept separate.")
        s.flush()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PricePulse admin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    p_ing = sub.add_parser("ingest")
    p_ing.add_argument("files", nargs="+")
    p_ing.add_argument("--region")
    sub.add_parser("report")
    sub.add_parser("review")
    p_m = sub.add_parser("merge")
    p_m.add_argument("keep", type=int)
    p_m.add_argument("drop", type=int)
    p_users = sub.add_parser("users")
    p_users.add_argument(
        "--list", action="store_true", help="also list the newest accounts (ID, age check, plan)"
    )
    p_purge = sub.add_parser("purge-sample")
    p_purge.add_argument(
        "--yes", action="store_true", help="really remove them (without it: only show counts)"
    )
    p_plan = sub.add_parser("plan")
    p_plan.add_argument("uid")
    p_plan.add_argument(
        "plan",
        choices=["paid", "free", "tester"],
        help="paid = unlimited sessions; tester = unlimited sessions AND unlimited items; free = normal",
    )
    p_so = sub.add_parser("store-owner", help="let a signed-in person run a local store's portal")
    p_so.add_argument("store_id", type=int)
    p_so.add_argument("uid")
    p_so.add_argument("--remove", action="store_true", help="take the access away instead")
    p_paid = sub.add_parser("store-paid", help="set a local store's paid-until date (YYYY-MM-DD, or none)")
    p_paid.add_argument("store_id", type=int)
    p_paid.add_argument("date")
    p_ap = sub.add_parser(
        "store-approve", help="approve a store that signed up: shown to shoppers until the date"
    )
    p_ap.add_argument("store", help="the store's number (id) or its 13-digit Store ID")
    p_ap.add_argument("date", help="paid until, YYYY-MM-DD")
    sub.add_parser("stores", help="list the local stores: plan, paid until, owners, items")
    p_role = sub.add_parser(
        "admin-role", help="make an account an admin of the PricePulse admin area (or not)"
    )
    p_role.add_argument("uid")
    p_role.add_argument("state", choices=["on", "off"])
    p_re = sub.add_parser("rematch", help="re-run today's matching rules over everything already loaded")
    p_re.add_argument(
        "--yes", action="store_true", help="really do it (without it: only show what would happen)"
    )
    p_req = sub.add_parser("requests", help="items shoppers asked for that we do not list yet")
    p_req.add_argument("--add", metavar="TERM", help="add this search term to products.txt and mark it added")
    p_req.add_argument("--category", default="Groceries", help="category for --add (default Groceries)")
    p_req.add_argument(
        "--done", metavar="TERM", help="mark requests for this term as added (run on the server)"
    )
    p_req.add_argument("--decline", metavar="TERM", help="mark this request declined (not added)")
    p_req.add_argument("--file", default=None, help="products file (default products.txt next to admin.py)")
    args = ap.parse_args(argv)

    settings = get_settings()
    get_engine(settings.database_url)
    Session = session_factory(settings.database_url)
    if args.cmd == "init-db":
        print(f"Database ready: {settings.database_url}")
        return 0
    with Session.begin() as s:
        if args.cmd == "ingest":
            for f in args.files:
                rep = ingest_file(s, Path(f), settings, region=args.region)
                print(f"{f}: {rep.summary()}")
        elif args.cmd == "report":
            r = match_report(s)
            print(f"{r['listings']} store listings grouped into {r['products']} products.")
            print(
                f"{r['products_at_2plus_stores']} products are sold by two or more stores "
                f"({r['match_rate']}% of products can be compared)."
            )
            print(f"{r['needs_review']} listings wait for review. Run: python admin.py review")
            print("By match type: " + ", ".join(f"{k} {v}" for k, v in sorted(r["by_status"].items())))
        elif args.cmd == "review":
            review(s)
        elif args.cmd == "purge-sample":
            n = purge_sample(s)
            text = (
                f"{n['prices']} example prices, {n['listings']} listings, {n['products']} products "
                f"and {n['runs']} example scrape records"
            )
            if args.yes:
                print(f"Removed {text}. Accounts and real prices were not touched.")
            else:
                s.rollback()
                print(f"Would remove {text}. Nothing was changed. Add --yes to do it.")
        elif args.cmd == "admin-role":
            from pricepulse.db import AppUser

            user = s.get(AppUser, args.uid)
            if user is None:
                print(f"No account with ID {args.uid}. Find it with: admin.py users --list")
                return 1
            user.is_admin = args.state == "on"
            print(f"Account {args.uid}: admin area {'ON' if user.is_admin else 'OFF'}.")
        elif args.cmd == "rematch":
            from pricepulse.ingest import rematch

            before = match_report(s)
            rep = rematch(s, settings)
            after = match_report(s)
            print(rep.summary())
            print(
                f"Products {before['products']} -> {after['products']}; sold by two or more stores "
                f"{before['products_at_2plus_stores']} ({before['match_rate']}%) -> "
                f"{after['products_at_2plus_stores']} ({after['match_rate']}%)."
            )
            if args.yes:
                print("Done. Prices, accounts and lists were not touched.")
            else:
                s.rollback()
                print("Preview only. Nothing was changed. Add --yes to do it (take a backup first).")
        elif args.cmd == "requests":
            from pricepulse.db import ItemRequest

            def mark(term: str, status: str) -> int:
                rows = s.scalars(
                    select(ItemRequest).where(
                        ItemRequest.key == term.strip().lower(), ItemRequest.status == "open"
                    )
                ).all()
                for r in rows:
                    r.status = status
                return len(rows)

            if args.add:
                path = Path(args.file) if args.file else Path(__file__).with_name("products.txt")
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(f"\n# category: {args.category}\n{args.add.strip()}\n")
                n = mark(args.add, "added")
                print(f"Added '{args.add.strip()}' to {path.name} ({n} request(s) marked added).")
                print("Commit and push products.txt; it appears after the next nightly scrape and upload.")
                print(f'On the server, mark it added:  admin.py requests --done "{args.add.strip()}"')
            elif args.done:
                print(f"Marked added: '{args.done.strip()}' ({mark(args.done, 'added')} request(s)).")
            elif args.decline:
                n = mark(args.decline, "declined")
                print(f"Declined '{args.decline.strip()}' ({n} request(s)).")
            else:
                rows = s.execute(
                    select(ItemRequest.key, func.count(), func.min(ItemRequest.term))
                    .where(ItemRequest.status == "open")
                    .group_by(ItemRequest.key)
                    .order_by(func.count().desc(), ItemRequest.key)
                ).all()
                if not rows:
                    print("No open requests.")
                for _key, n, term in rows:
                    print(f"{n:>3} x  {term}")
                if rows:
                    print('To add one:  python admin.py requests --add "white mushrooms 250g"')
                    print('To refuse:   python admin.py requests --decline "white mushrooms 250g"')
        elif args.cmd == "users":
            from pricepulse.db import AppUser, Subscription, utcnow

            users = s.scalars(select(AppUser)).all()
            paying = s.scalar(
                select(func.count()).select_from(Subscription).where(Subscription.paid_until > utcnow())
            )
            checked = sum(1 for u in users if u.id_fingerprint)
            minors = sum(1 for u in users if u.id_fingerprint and not u.adult)
            by_hand = sum(1 for u in users if u.plan_override == "paid")
            print(f"{len(users)} accounts. {checked} have done the age check, {minors} are under 18.")
            print(f"{paying} paying subscribers, {by_hand} given the Unlimited plan by hand.")
            if args.list:
                print("Newest accounts first: ID | created | age check | plan")
                for u in sorted(users, key=lambda x: x.created_at, reverse=True)[:15]:
                    check = "done" if u.id_fingerprint else "not yet"
                    print(
                        f"  {u.uid} | {u.created_at:%d %b %H:%M} | {check} | {u.plan_override or 'normal'}"
                        + (" | ADMIN" if u.is_admin else "")
                    )
        elif args.cmd == "store-owner":
            from pricepulse.db import AppUser, Store, StoreOwner

            store, user = s.get(Store, args.store_id), s.get(AppUser, args.uid)
            if store is None or store.kind != "local":
                print(f"No local store with number {args.store_id}. List them with: admin.py stores")
                return 1
            if args.remove:
                link = s.get(StoreOwner, args.uid)
                if link is not None and link.store_id == store.id:
                    s.delete(link)
                print(f"{args.uid} no longer runs {store.name}.")
            else:
                if user is None:
                    print(f"No account with ID {args.uid}. The person must sign in to PricePulse once first.")
                    return 1
                link = s.get(StoreOwner, args.uid)
                if link is None:
                    s.add(StoreOwner(uid=args.uid, store_id=store.id))
                else:
                    link.store_id = store.id
                print(f"{args.uid} now runs {store.name}. They find it under My store in the menu.")
        elif args.cmd == "store-paid":
            from datetime import datetime, timedelta

            from pricepulse.db import Store

            store = s.get(Store, args.store_id)
            if store is None or store.kind != "local":
                print(f"No local store with number {args.store_id}. List them with: admin.py stores")
                return 1
            if args.date.lower() in ("none", "off", "-"):
                store.paid_until = None
                print(f"{store.name} has no expiry date.")
            else:
                try:
                    store.paid_until = datetime.strptime(args.date, "%Y-%m-%d") + timedelta(
                        hours=23, minutes=59
                    )
                except ValueError:
                    print("Type the date like 2026-12-31.")
                    return 1
                print(f"{store.name} is paid until {args.date} (hidden from shoppers 3 days after that).")
        elif args.cmd == "store-approve":
            from datetime import datetime, timedelta

            from pricepulse.db import Store

            key = str(args.store)
            store = s.scalar(select(Store).where(Store.store_number == key)) if len(key) == 13 else None
            if store is None and key.isdigit() and len(key) < 13:
                store = s.get(Store, int(key))
            if store is None or store.kind != "local":
                print(f"No local store {key}. List them with: admin.py stores")
                return 1
            try:
                until = datetime.strptime(args.date, "%Y-%m-%d") + timedelta(hours=23, minutes=59)
            except ValueError:
                print("Type the date like 2027-01-31.")
                return 1
            store.status, store.active, store.paid_until, store.reject_reason = "approved", True, until, ""
            print(f"{store.name} is approved and visible until {args.date} (plus 3 grace days).")
        elif args.cmd == "stores":
            from pricepulse.db import Store, StoreOwner
            from pricepulse.localstores import active_count

            for st in s.scalars(select(Store).where(Store.kind == "local").order_by(Store.id)).all():
                owners = [
                    o.uid for o in s.scalars(select(StoreOwner).where(StoreOwner.store_id == st.id)).all()
                ]
                paid = st.paid_until.date().isoformat() if st.paid_until else "no expiry"
                print(
                    f"{st.id} | {st.store_number or '-'} | {st.status} | {st.name} | "
                    f"{st.town or st.region} | {st.category or '-'} | "
                    f"{active_count(s, st)}/{st.max_items} items | paid until {paid} | "
                    f"{'ON' if st.active else 'OFF'} | owners: {', '.join(owners) or 'none'}"
                )
        elif args.cmd == "plan":
            from pricepulse.db import AppUser

            user = s.get(AppUser, args.uid)
            if user is None:
                print(f"No account with ID {args.uid}. The person must sign in to PricePulse once first.")
                return 1
            user.plan_override = {"paid": "paid", "tester": "tester"}.get(args.plan, "")
            what = {
                "paid": "the Unlimited plan",
                "tester": "the TESTER plan (unlimited sessions and items)",
            }.get(args.plan, "the normal plan")
            print(f"Account {args.uid} is now on {what}.")
        elif args.cmd == "merge":
            n = merge(s, args.keep, args.drop)
            print(f"Moved {n} listings from product {args.drop} into {args.keep}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
