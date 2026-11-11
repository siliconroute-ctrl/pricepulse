"""Admin tasks for PricePulse ZA, in plain language.

python admin.py init-db                 create or upgrade the database
python admin.py ingest data/latest_paarl.json
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
    sub.add_parser("users")
    p_purge = sub.add_parser("purge-sample")
    p_purge.add_argument(
        "--yes", action="store_true", help="really remove them (without it: only show counts)"
    )
    p_plan = sub.add_parser("plan")
    p_plan.add_argument("uid")
    p_plan.add_argument("plan", choices=["paid", "free"])
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
        elif args.cmd == "plan":
            from pricepulse.db import AppUser

            user = s.get(AppUser, args.uid)
            if user is None:
                print(f"No account with ID {args.uid}. The person must sign in to PricePulse once first.")
                return 1
            user.plan_override = "paid" if args.plan == "paid" else ""
            print(
                f"Account {args.uid} is now on the {'Unlimited' if args.plan == 'paid' else 'normal'} plan."
            )
        elif args.cmd == "merge":
            n = merge(s, args.keep, args.drop)
            print(f"Moved {n} listings from product {args.drop} into {args.keep}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
