# Local stores: the weekly price list

Local (private) stores can compete with the big chains. Each week a store owner sends a spreadsheet of
the items they sell most, with their own selling prices. Shoppers in that store's town see those prices
next to Checkers, Pick n Pay and the rest, with a **Local store** label.

## What you do (admin, on a computer)
1. **Admin area, Local stores, Add a local store.** Fill in the store name, its **region** (the town its
   shoppers are in), contact person, phone and email.
2. When the store's file arrives (email, WhatsApp...), save it, then tap **Load weekly file** on that
   store and choose it.
3. The screen shows a **preview** first: how many rows, how many are new, how many are already known,
   how many are also sold by a chain, how many items will be hidden because they are missing from this
   file, and any rows skipped (with the reason). Check it, then tap **Load the file**.
4. Shoppers in that region see the prices straight away. **History** shows every file received.

Mistakes are cheap: a file only changes that store's own list. You can load a corrected file the same day.

## The file
A CSV (or an Excel sheet saved as "CSV"). The first row names the columns. Only **item** and **price**
are required.

| Column | Meaning | Example |
|---|---|---|
| `item` | what the item is called | `Clover Full Cream Milk` |
| `price` | the **selling price** today, in rand | `34.50` (also `R 34,50`) |
| `size` | pack size | `2L`, `500g`, `6 x 330ml` |
| `barcode` | the item's barcode, if the store has it (best for matching) | `6001234567890` |
| `sku` | the store's own item code | `MILK2` |
| `department` | the store's section | `Dairy`, `Butchery`, `Bakery` |
| `category` | `Groceries`, `Fresh`, `Health & Beauty` or `Liquor` (blank means Groceries) | `Fresh` |
| `promo price` | a special price, lower than `price` | `79.90` |

**Pictures:** an optional **`image`** column (also `ImageUrl`, `image link`, `photo`, `picture`) holds a web address of the item\u2019s picture. It must start with `https://`, have no spaces and be under 400 characters; anything else is counted as "not usable" in the preview and the item still loads without a picture. A file with no picture keeps the picture already stored. Pictures are shown small on the item card and larger on the product page, and disappear quietly if the other website stops serving them (PricePulse does not copy them). Uploading picture files ourselves is the next step.

More columns are understood when a file has them: **`brand`** (manufacturer) and **`stock`**. A row
whose stock says "No Stock", "Out of stock", "Discontinued" or "0" is **left out**, so it is not shown to
shoppers. Headers are matched by meaning, ignoring capitals, spaces and punctuation (`PriceInclVAT`,
`Price incl. VAT` and `price` all work). `Description` is read as the item name and `Product` as the item
code when a file has both. Extra columns (image links, dates) are ignored.

**Categories.** Our four (Groceries, Fresh, Health & Beauty, Liquor) keep their names. Any other category
text, such as a wholesaler's own sections, is kept as the store wrote it, and the preview lists it so you
can spot typos. **Prices must be selling prices including VAT**, as a shopper would pay: a wholesaler's
cost or ex-VAT price list is not what shoppers should see.

Download a ready-made template from **Local stores, Download the CSV template**
(`docs/templates/local-store-template.csv` in the repository). Up to 5000 rows per file (a 5000-row file takes
about 15 seconds to check; split bigger lists). Commas, semicolons or tabs all work; the separator is the
one found on every line, so commas inside descriptions do not confuse it.

## Rules
- **Each file replaces the store's list.** Items not in the new file are hidden from shoppers (their price
  history is kept). Untick "Replace the store's list" in the preview to only add and update.
- A store's prices are marked **old after 8 days**, so a weekly file keeps them fresh.
- Items with an unknown category, a price outside R0.50 to R100 000, or a web address in the name are skipped.
- Liquor items are only shown to people 18 and over, as everywhere else in the app.
- A local store's prices are shown in **its own region only**.

## A short note you can send to a store owner
> Hi! PricePulse shows shoppers in our town where an item is cheapest. We would like your best-selling
> items on it. Once a week please send us a spreadsheet (CSV) with: item name, price, and if you have
> them size, barcode, your item code and department. We load it and shoppers see your prices next to the
> big stores. Template attached. You decide what to send; send an updated file any time, and we can
> switch you off whenever you ask.

## Later
The same checks will serve the **store-owner API** (one secret key per store) and a self-service input
screen. See `docs/DATA_MODEL.md`.
