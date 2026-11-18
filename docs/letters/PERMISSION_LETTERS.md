# Letters asking the stores for permission (drafts)

**Send these from admin@trolleyup.com, from you, in your own name.** Find each store's right address on its
website (look for *Contact us*, *Corporate affairs*, *Online / e-commerce*, *Partnerships* or *Legal*).
Keep every reply. Have an attorney glance at the wording before you send, as you plan to do with the Terms.

Why we are asking: some stores' `robots.txt` files ask automated programs not to use their search pages
(Pick n Pay and Woolworths say so; Makro says so for its search pages). `robots.txt` is a request, not a
law, but asking for permission is the right and safe thing to do. Until a store answers, you decide
whether PricePulse keeps reading its public pages. If a store says no, say so to Claude and the scraper
for that store is switched off.

What every letter promises (all true today): once a night, one home computer, one search at a time with
pauses, no login, no personal data, the price shown with the date and a link back to the store's own page,
and an immediate stop on request.

---

## Letter 1: the general letter (change the bits in [brackets])

**Subject:** Request for permission to show your public prices in PricePulse ZA

Dear [Name / Online team],

I am the founder of PricePulse ZA (pricepulse.trolleyup.com), a small South African app that helps
shoppers see where everyday items are cheapest in their town. It is in private testing.

To show prices, a program on one computer visits your public website once a night, between about 02:30 and
05:00. It searches for about 230 everyday items, one search at a time with pauses, and reads the item name,
size, price and the link to your product page. It does not log in, does not look at any customer
information, and does not place orders. The app shows each price with the date it was collected and a link
back to your own product page.

I would like to ask:

1. May PricePulse continue to read your public product pages in this way?
2. Do you offer a product and price feed, partner programme or API that we should use instead?
3. Are there conditions you would like us to follow (times, number of requests, how we name ourselves)?

If you would rather we did not, please tell me and I will stop straight away. Our visits identify
themselves as PricePulse ZA and we can supply our contact details to your technical team.

Thank you for your time.

Kind regards,
Joao M. de Gois
PricePulse ZA, Paarl
admin@trolleyup.com

---

## Letter 2: Pick n Pay

Use Letter 1 and add this paragraph after the second paragraph:

> We are aware that your robots.txt asks automated visitors not to use your search pages. Our program
> currently reads search results for our list of items, once a night, at a low rate. I would like to do this
> with your permission, or to switch to a feed or another route you prefer.

## Letter 3: Woolworths

Use Letter 1 and add:

> We are aware that your robots.txt asks automated visitors not to use search addresses containing
> "searchterm". Our program currently reads search results for our list of items, once a night, at a low
> rate. I would like to do this with your permission, or to switch to a feed or another route you prefer.

## Letter 4: Checkers (Shoprite)

Use Letter 1 and add:

> Our visits cover your public product search once a night. We have also met your bot-protection page on
> occasion; we stop whenever we meet it and never try to get past it. A permitted route would be better
> for everyone.

## Letter 5: Dis-Chem

Use Letter 1 and add:

> We read your public search pages for health and beauty items only (about 16 items a night). We show the
> price you display for the product and never any loyalty discount.

## Letter 6: Clicks

**Subject:** Request for permission or a price feed: blocked request on 16 November 2026

Use Letter 1 and add this paragraph after the second paragraph:

> On 16 November 2026 at about 12:10 (SAST) our first three requests to clicks.co.za were blocked by your
> firewall ("Sorry, you have been blocked", Cloudflare Ray ID a5b5b7832ff94bcb). We stopped at once and have
> not tried again. We would like to include Clicks health and beauty items (about 16 searches a night), but
> only with your agreement. A price feed or an affiliate or partner data programme would suit us best.

## Letter 7: Makro (Massmart)

Use Letter 1 and add:

> We have read your robots.txt, which asks automated visitors not to use your search pages. We would not
> use them. We would like to ask whether you permit reading specific product or category pages for a short
> list of items, or whether you offer a feed or partner route. Until you reply we do not read your site.

---

## When a store replies

| Reply | What happens next |
|---|---|
| "Yes, carry on" | Keep the email. Tell Claude. Nothing changes. |
| "Use this feed / API" | Forward it to Claude. We build a reader for it, which is more reliable than the website. |
| "Please stop" | Tell Claude the same day. The scraper for that store is switched off and its prices stop being updated. |
| No reply after a month | Send one polite reminder. Keep records of every message you sent. |

## Local stores are different
Prices sent by a local store come from the store itself, so no permission is needed beyond their own
agreement to send them. See `docs/LOCAL_STORES.md`.
