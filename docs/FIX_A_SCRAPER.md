# How to fix a broken scraper

Websites change. When one does, a store suddenly finds 0 products, or wrong prices.
This guide fixes that in about 20 minutes. You do not need to understand all the code.

## 1. See what is wrong

Run just that store, with details:

```bash
python run_scrapers.py --region paarl --store "Pick n Pay" --limit 3 --debug --no-db
```

You will see one of these:

| You see | Meaning | Go to |
|---|---|---|
| `0 of 3 products found` | The page loaded, but we could not find the products on it | Step 2 |
| Rows with silly prices (R0.39, R12 999) | We are reading the wrong part of the card | Step 3 |
| `BLOCKED by the website` | The store refused us | Step 5 |
| `Could not open the website` | Site down, or the address changed | Check the store's home page in your own browser |

## 2. Look at the saved page

When nothing is found, the scraper saves two files:

```
debug/pick-n-pay/clover_full_cream_milk_2l.png    <- picture of what the browser saw
debug/pick-n-pay/clover_full_cream_milk_2l.html   <- the page itself
```

Open the **picture** first.

- **Cookie banner or "choose your store" popup covering everything?** The scraper must
  close it. Add the button text to `_dismiss_cookies` in `pricepulse/scrapers/base.py`,
  or override `setup()` in the store's class (see step 4).
- **Products are visible?** Then only the "selectors" (the addresses of the parts we
  read) are wrong. Continue.

Now open the same search in **Chrome on your computer**:

1. Go to the store's site and search for `milk`.
2. Right-click the **name** of the first product, choose **Inspect**.
3. The panel highlights a line like
   `<a class="product-name__title" href="/p/123">Clover Full Cream Milk 2L</a>`.
   The part after `class="` is what we need: `product-name__title`.
4. Move up a few lines in the panel until the highlighted box covers the **whole
   product card** (picture, name and price). Note its class, e.g. `product-card`.
5. Right-click the **price** and Inspect it too. Note its class, e.g. `price__now`.

## 3. Update the selectors

Open `pricepulse/scrapers/stores.py` and find the store's class. Put your classes
**first** in each list (a dot before a class name):

```python
selectors = {
    "card":  [".product-card", ...old ones...],
    "name":  [".product-name__title", ...],
    "price": [".price__now", ...],
    "promo": [...],   # promo price, when the card shows "was / now"
    "was":   [...],   # the old crossed-out price
    "size":  [],      # only if the size is shown separately from the name
    "link":  ["a.product-name__title", "a"],
}
```

Run step 1 again. Repeat until the rows look right.

**Better: use the store's own data feed.** In Chrome, open **Inspect → Network**, tick
**Fetch/XHR**, then search again on the site. Click the requests that appear. If one
returns a list of products with names and prices (it looks like `{"products":[...]}`),
copy its address into `api_url` with `{q}` where the search word goes. These feeds
change less often than page layouts. The scraper tries `api_url` first and falls
back to the page.

## 4. Region or branch prices

Some stores price per branch. If the site asks for a suburb, postal code or store:

1. Find out how the site remembers it (often a `storeCode=` in the data-feed address,
   or a cookie).
2. Put that setting per town in `regions.yaml`, for example:
   ```yaml
   paarl:
     stores:
       Pick n Pay: {store_code: "WC21"}
   ```
3. Set the store's `regional_pricing` at the top of `regions.yaml` to `branch` or
   `postcode`. If prices are the same everywhere, set `national`.
4. Use the setting in the URL (`{store_code}`) or in the class's `setup()` method.

## 5. When a store blocks us

Do **not** try to get around a block (no CAPTCHA solving, no fake logins, no
proxy farms). The scraper already backs off and gives up politely after 3 refusals.

- Wait a day and try again; temporary blocks clear.
- Check the store's terms of use. If automated collection is not allowed, remove
  the store from `ALL_SCRAPERS` in `stores.py` and ask the retailer for a data feed.

## 6. Lock it in with a test

So the fix stays fixed:

```bash
python run_scrapers.py --region paarl --store "Pick n Pay" --limit 1 --save-fixtures --no-db
```

This saves the real page in `tests/fixtures/live/pick-n-pay/`. Copy it over the
matching file in `tests/fixtures/pick-n-pay/`, update the expected names and prices
in `tests/test_scraper_parsing.py`, then run:

```bash
python -m pytest tests/test_scraper_parsing.py
```

When a store has been checked against the live site, set `verified = True` on its
class. The `/api/v1/health` page shows which stores are verified.

## 7. Prices that look wrong

The health report (`data/health_paarl.json`) lists **suspect** prices under R1 or
over R5000. Common causes: the card shows a price per kg and a pack price and we read
the wrong one, or cents are in a separate element. Point `price` at the exact
element in step 3.
