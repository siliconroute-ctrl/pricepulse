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

## Opening hours and shop photos (app v41)
In **My store → Store details** the owner now sets:
- **Opening hours** for each day (24-hour clock, South African time, for example 08:00 to 17:00), a "closed all day" tick, and an optional note such as "Closed on public holidays". Shoppers see "Open now · closes at 17:00", "Closed · opens tomorrow at 08:00" and the whole week under "See all hours".
- **Shop photos** (one or two): a clear photo of the front of the shop. The browser shrinks it before sending (JPEG only reaches the server, 600 KB at most). Only upload photos you have the right to publish.
- A **Description** on each item (up to 500 characters), shown on the item's View page.

Shoppers reach the store page by tapping the store name on an item, and the **Enter Store** button lists that store's items by type. Photos live in `/data/uploads/stores/<store id>/` and are included in the backup (`pricepulse-uploads-DATE.tar.gz`, see docs/DEPLOY.md).

## Store owners sign up by themselves (app v42)
1. On the sign-in screen the owner taps **I run a store: list my items**, signs in or creates an account (Google or email), and fills in one form: store name, **one category** (Electronics, Stationery, Pets, Health & Beauty, Fresh, Liquor, Groceries), town (from `backend/towns.yaml`), address, phone, optional WhatsApp, contact, email, and a liquor licence number for Liquor. A signed-in person who is not yet a store owner also finds **List my store** in the three-dot menu.
2. They get a **13-digit Store ID** (12 digits plus a check digit). It is the reference for the store everywhere (admin list, `pp admin stores`, invoices). Their password stays their Google or email password; PricePulse never stores it.
3. The store is **Waiting for approval**: hidden from shoppers, but the owner can add items, upload a CSV, set hours and photos.
4. You see it under **Admin, Local stores, Waiting for approval** (and `pp admin stores`). **Approve** asks for the paid-until date (set it after you invoice R149); **Reject** takes a reason the owner sees. Same from the desktop: `pp admin store-approve <store number or 13-digit ID> 2027-01-31`.
5. After approval the store is shown to shoppers whose town is within **100 km** (`PP_LOCAL_RADIUS_KM`) of the store's town. Prices are written once for every shopper town in range (`pricepulse/towns.py`).
6. **Check `backend/towns.yaml`**: the coordinates are approximate town centres written from memory; correct them before go-live and add any town you need.
7. Rules: a Liquor store needs a licence number and an owner who passed the age check, and is shown to adults only; Health & Beauty stores may not list prescription medicines (Store terms). Terms and Privacy are Version 6 (attorney review).
