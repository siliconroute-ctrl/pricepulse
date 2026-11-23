"""Category fixes: water is groceries, never fresh produce."""

from conftest import row
from sqlalchemy import select

from pricepulse.categories import adjust_category
from pricepulse.db import Listing, Product, session_factory, utcnow
from pricepulse.ingest import ingest_items, rematch


def test_water_is_never_fresh():
    assert adjust_category("aQuellé Watermelon Flavoured Sparkling Water 500ml", "Fresh") == "Groceries"
    assert adjust_category("Still Water 5L", "Fresh") == "Groceries"
    assert adjust_category("Watermelon 1kg", "Fresh") == "Fresh"  # the fruit is still fresh
    assert adjust_category("Castle Lite 6 x 340ml", "Liquor") == "Liquor"
    assert adjust_category("Rose Water 100ml", "Health & Beauty") == "Health & Beauty"


def test_ingest_files_flavoured_water_under_groceries_and_rematch_fixes_old_data(settings):
    rows = [row("Checkers", "aQuellé Watermelon Flavoured Sparkling Water 500ml", 12.99, category="Fresh")]
    with session_factory(settings.database_url).begin() as s:
        ingest_items(s, rows, "paarl", utcnow(), settings)
        p = s.scalars(select(Product)).one()
        assert p.category == "Groceries"
        # data loaded before the rule existed
        p.category = "Fresh"
        s.scalars(select(Listing)).one().category = "Fresh"
    with session_factory(settings.database_url).begin() as s:
        rematch(s, settings)
        assert s.scalars(select(Product)).one().category == "Groceries"
        assert s.scalars(select(Listing)).one().category == "Groceries"
