# Phase 6 plan: Google Play (not part of this delivery)

The web app is already installable on Android from Chrome. A Play Store listing
wraps the same app; no rewrite is needed.

## Recommended route: Trusted Web Activity with Bubblewrap

A Trusted Web Activity (TWA) shows the live web app full-screen inside a tiny Android
app. Updates to the website reach users without a new Play release.

### Prerequisites

1. **Google Play developer account**: one-off USD 25 fee, identity verification
   (allow a few days). https://play.google.com/console
2. **The app on HTTPS at a stable address** (Option A or B in DEPLOY.md).
3. **Privacy policy page** at a public URL. It can be short: no accounts, no personal
   data, location used only on the phone, shopping list stored on the phone,
   server logs without IP addresses.
4. **Data safety form** in Play Console. Expected answers: no data collected or
   shared; location is "processed on device only" (not collected); no ads at first.
5. **Content rating questionnaire**: the app lists alcohol prices, so expect an
   adult-content flag for alcohol. Consider an 18+ notice on the Liquor category.
6. **Store listing**: icon 512×512 (use `frontend/icons/icon-512.png`), feature
   graphic 1024×500, at least 2 phone screenshots, short and full descriptions.
7. **Testing track**: Google requires new personal developer accounts to run a closed
   test with at least 12 testers for 14 days before going to production.

### Steps

```bash
npm install -g @bubblewrap/cli
bubblewrap init --manifest https://prices.siliconroute.co.za/manifest.webmanifest
bubblewrap build
```

Bubblewrap creates a signing key (back it up safely; losing it means you cannot
update the app) and an `assetlinks.json` file. Publish that file at
`https://prices.siliconroute.co.za/.well-known/assetlinks.json` so Android trusts
the site (otherwise a browser bar shows at the top). Upload the `.aab` file to a
closed testing track in Play Console.

## Alternative: Capacitor

Choose Capacitor only if the app later needs native features a web app cannot do
(for example background price-drop notifications). It bundles the web files into
the app, so every update needs a new Play release.
