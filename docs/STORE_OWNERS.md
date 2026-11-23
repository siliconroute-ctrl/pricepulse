# Store owners on PricePulse (paying local stores)

A local store lists its own items and prices inside PricePulse. Shoppers find the store's items in the
search, tap the store name to see its page (address, Call, WhatsApp) and use **Enquire about this item**
to message the store. Shoppers deal directly with the store: no cart, no payment through PricePulse.

## The plan (first client, 23 Nov 2026)
- R149 a month per store, up to **200 items** (the store's `max_items`).
- Invoiced by hand. The PricePulse owner sets the **paid-until** date; the store stays visible for **3
  days** after it, then its items are hidden (the owner still sees and can download everything).
- PayFast subscriptions for stores are not built.

## Setting up a new store (PricePulse owner)
1. In the app: three-dot menu, **Admin area**, **Local stores**, **Add a local store**. Fill in the name,
   region (the town its shoppers are in), town, address, phone, **type of store**, **most items** (200),
   **category of the store's own items** (for example `Electronics`), WhatsApp number if different from the
   phone, and **paid until**.
2. The client signs in to PricePulse once (Google or email, the same as shoppers). No ID number is needed.
3. Find the client's sign-in ID: `pp admin users --list` (newest first). Link them:
   `pp admin store-owner <store number> <sign-in ID>` (`--remove` takes access away).
   Or type the sign-in ID into the store's Edit dialog.
4. The client sees **My store** in the three-dot menu. Check with `pp admin stores`.
5. Renewing: `pp admin store-paid <store number> 2027-01-31` (`none` = no end date).

## What the client does (My store, best on a computer)
- **Items**: Add an item (name, type, New or Refurbished, price **including VAT**, special price, size or
  specification, item code, barcode, picture link, in stock). Edit, Remove, search, download the list.
- **Upload CSV**: choose a file, read the preview, tap Load. Columns: `item`, `price` (needed), and for
  Electronics `condition` (New or Refurbished, needed) and `sub-category`, plus `size`, `sku`, `barcode`,
  `promo price`, `image`. Items with the same `sku` are updated. Nothing is hidden unless the client ticks
  "Replace my whole list". Template: `docs/templates/store-owner-template.csv`.
- **Store details**: the address, phone and WhatsApp number shoppers see.
- **My prices are still correct**: one tap writes today's date on every price so none turns "old"
  (prices show "Old price" after 8 days).

## Electronics sub-categories
Laptops, Desktops, Screens, Peripherals, Mobile phones (Samsung / Android), iPhones. Each item is also
labelled New or Refurbished. Shoppers tap **Electronics**, pick a type and filter by condition. The list
lives in `backend/pricepulse/categories.py` (`SUBCATEGORIES`) so a bigger category database can replace it.

## Rules the client agrees to (Terms, "Stores that sell through PricePulse")
Prices are the selling price including VAT and other taxes, honest and current; items are lawful and
described truthfully (including new or refurbished); the store, not PricePulse, sells and guarantees the goods.

## Welcome message (WhatsApp-sized)
> Welcome to PricePulse ZA! Your store page is ready. 1) Open pricepulse.trolleyup.com on a computer and
> sign in with the same Google or email sign-in you used. 2) Tap the three dots, then **My store**.
> 3) Add your items one by one, or upload a CSV file (template attached). Remember: every price must be
> the price the buyer pays, VAT included. Questions: admin@trolleyup.com.
