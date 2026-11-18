# PricePulse ZA: how the data is organised

One shopper question drives the design: **"Where is this item cheapest, in my town?"**
So every price belongs to an item, a store, and a town (region). Chains and local stores share the
same tables; a local store is just a store that belongs to one region.

## The pieces

| Piece | Where it lives | What it holds |
|---|---|---|
| **Region** (town or metro) | `backend/regions.yaml` | slug, name, map position, postal code. Today: Paarl, Wellington and the other Western Cape towns. Metros come in phase 3. |
| **Store** | table `store` | `name`, `kind` (`chain` or `local`), and for local stores `region`, `town`, `address`, `contact_name`, `phone`, `email`, `notes`, `active`, `last_submission_at`, `api_key_hash` (phase 2) |
| **Department** | `listing.department` | the store's own section, e.g. Butchery, Bakery, Dairy (free text from the store) |
| **Category** | `listing.category`, `product.category` | one of Groceries, Fresh, Health & Beauty, Liquor |
| **Item (listing)** | table `listing` | one store's entry for an item: `name`, `size_text`, `sku` (the store's own item code), `barcode`, `department`, `category`, `url`, `source` (`scrape`, `csv`, `api`), `active`, plus how it was found (`term`, `hit_rank`) |
| **Product** (the shopper's card) | table `product` | the canonical item shoppers see: name, brand, size, category, `visibility`. Many listings, one per store, can sit on one product. |
| **Price** (selling price) | table `price` | `price_cents`, optional `promo_cents`, `region`, `scraped_at`, `listing_id`. A new row for every collection, so history is kept. |
| **Weekly file log** | table `store_submission` | for each file or API call from a local store: when, source, file name, rows, added, updated, hidden, skipped, and who loaded it |
| **Essential items** | table `essential_item` | the search words the chains' scrapers look up every night |

## How an item gets from a local store to a shopper

1. The store owner sends a CSV (or later calls the API) with: item name, selling price, and optionally
   size, barcode, SKU, department, category and promo price.
2. Each row is checked (name 2 to 120 characters, price R0.50 to R100 000, no web addresses, a known
   category). Bad rows are listed and skipped; good rows go on.
3. Matching, in this order: **same barcode** as another store's item, then a **confident name and size
   match**, otherwise the item gets a card of its own. So "Clover Full Cream Milk 2L" at a local shop lands
   on the same card as Checkers and Woolworths.
4. The price is saved for the **store's own region only**. Shoppers in other towns never see it.
5. Replace rule: items that were in the store's last file but are missing from the new one are
   hidden (`active = false`); their history stays. Prices are marked old after **8 days** (chains: 2 days).

## Rules that always hold

- A store never has two active listings on one product (the cheaper one is shown).
- The owner's hand-made decisions (Show or Hide, review answers) are never overwritten by a file.
- Shoppers only see products that have at least one active listing in an active store in their region.
- Nothing is ever deleted by an import: older prices stay as history.

## Planned (not built yet)

- **Phase 2:** the store-owner API, one secret key per store (`store.api_key_hash`), same checks as the CSV.
- **Phase 3:** more regions (metros), and per-region chain prices after a price-difference test.
