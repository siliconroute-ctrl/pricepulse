// Browser test of the whole app (Playwright). Run through run_smoke.py.
//   node tests/smoke.mjs http://127.0.0.1:8765  [screenshot-folder]
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const base = process.argv[2] || "http://127.0.0.1:8000";
const shots = process.argv[3] || "";
const launch = process.env.PP_CHROMIUM_PATH ? { executablePath: process.env.PP_CHROMIUM_PATH } : {};
let failures = 0;
function check(ok, what) { console.log((ok ? "PASS " : "FAIL ") + what); if (!ok) failures++; }
const shot = async (page, name, full = false) => { if (shots) await page.screenshot({ path: `${shots}/${name}.png`, fullPage: full }); };

// A valid, made-up South African ID number for a date of birth (YYMMDD).
function makeId(yymmdd) {
  const b = yymmdd + "5009" + "0" + "8";
  for (let z = 0; z < 10; z++) {
    const n = b + z; let t = 0;
    [...n].reverse().forEach((c, i) => { let d = +c; if (i % 2) { d *= 2; if (d > 9) d -= 9; } t += d; });
    if (t % 10 === 0) return n;
  }
}
const yy = (n) => String((new Date().getFullYear() - n) % 100).padStart(2, "0");
const ADULT = makeId("800101"), MINOR = makeId(yy(12) + "0315");
const meter = (page) => page.textContent("#meter");
async function signIn(page, name, id) {
  await page.goto(base + "/");
  await page.waitForSelector("#devform");
  await page.fill("#devform [name=name]", name);
  await page.click("#devform button");
  await page.waitForSelector("#idform");
  if (id) { await page.fill("#idform [name=idnum]", id); await page.check("#idform [name=terms]"); await page.click("#idform button.btn"); await page.waitForSelector(".pills"); }
}
async function lookUp(page, word) {
  await page.fill("#q", word);
  const btn = page.locator("button.lookup").first();
  await btn.waitFor();
  await btn.click();
}

const browser = await chromium.launch(launch);
const ctx = await browser.newContext({ viewport: { width: 412, height: 915 }, acceptDownloads: true });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));

// 1. Sign in, then the age check.
await page.goto(base + "/");
await page.waitForSelector("#devform");
check((await page.textContent("#main")).includes("3 lookup sessions a day"), "sign-in screen explains the free plan and R99");
check(!(await page.isVisible("nav.tabs")), "no tabs or prices before sign-in");
await shot(page, "1-signin");
await signIn(page, "tester", null);
check((await page.textContent("#main")).includes("throw it away"), "ID screen says the number is not kept");
await page.fill("#idform [name=idnum]", "8001015009086");
await page.check("#idform [name=terms]");
await page.click("#idform button.btn");
await page.waitForFunction(() => document.querySelector(".err").textContent.length > 0);
check(/not valid/.test(await page.textContent(".err")), "wrong ID number is refused with a plain message");
await shot(page, "2-idcheck");
await page.fill("#idform [name=idnum]", ADULT);
await page.click("#idform button.btn");
await page.waitForSelector(".pills");
check(/3 free sessions left today/.test(await meter(page)), "meter before first lookup: " + await meter(page));

// 2. Look up a price.
await page.fill("#q", "milk");
await page.locator("button.lookup").first().waitFor();
check(!/R \d/.test(await page.textContent(".grid")), "no prices shown until an item is looked up");
await shot(page, "3-find");
await page.locator("button.lookup").first().click();
const cardEl = page.locator("article.card:has(.best)").first();
await cardEl.waitFor();
check(await cardEl.locator(".badge").count() >= 3, "looked-up milk shows at least 3 stores");
check((await cardEl.textContent()).includes("Best price"), "best price is labelled in text, not only colour");
check(/Session 1 of 3 today · 1 of 45 items/.test(await meter(page)), "meter after lookup: " + await meter(page));
await shot(page, "4-looked-up");

// 3. List, cheapest store, detail, towns.
await lookUp(page, "bread");
await page.locator("article.card:has(.best)").first().waitFor();
await page.getByRole("button", { name: /^List/ }).click();
const single = await page.locator("#single").textContent();
check(/Cheapest/.test(single) && /R \d/.test(single), "cheapest single store shown: " + single.replace(/\s+/g, " ").trim().slice(0, 50));
check(await page.locator("#split").count() === 1, "split shop shown");
await page.locator('[data-inc]').first().click();
await page.waitForFunction(() => document.querySelector(".qty b").textContent === "2");
check(true, "quantity can be changed");
await shot(page, "5-list", true);
await page.locator(".li a").first().click();
await page.waitForSelector("svg.chart", { timeout: 10000 }).catch(() => {});
check(await page.locator("svg.chart").count() === 1, "price history chart drawn");
await page.getByRole("button", { name: /^Towns/ }).click();
await page.waitForSelector(".town", { timeout: 10000 }).catch(() => {});
check(await page.locator(".town").count() >= 1, "towns tab shows town totals for the list");

// 4. Send to TrolleyUp.
await page.getByRole("button", { name: /^List/ }).click();
await page.getByRole("button", { name: "Send to TrolleyUp" }).click();
const dl = page.waitForEvent("download");
await page.getByRole("button", { name: "Make the file" }).click();
const file = await dl;
const csv = require("fs").readFileSync(await file.path(), "utf8");
check(/^pricepulse-for-trolleyup-\d{4}-\d\d-\d\d\.csv$/.test(file.suggestedFilename()), "export file name: " + file.suggestedFilename());
check(csv.replace(/^﻿/, "").startsWith("Item,Quantity,Store,Note\r\n") && csv.includes("PricePulse price R"), "export has TrolleyUp's columns and the price note");
await page.waitForFunction(() => document.querySelector("#dlg").textContent.includes("Import items from a CSV file"));
check(true, "steps for importing in TrolleyUp are shown");
await shot(page, "6-export");
await page.getByRole("button", { name: "Done" }).click();

// 5. Clear and the 3-a-day limit, then upgrade.
for (let n = 1; n <= 3; n++) {
  await page.getByRole("button", { name: /^List/ }).click();
  await page.getByRole("button", { name: "Clear session" }).click();
  await page.locator('#dlg [data-dlg="doclear"]').click();
  await page.waitForFunction(() => !document.querySelector(".li"));
  if (n < 3) { await lookUp(page, "milk"); await page.locator("article.card:has(.best)").first().waitFor(); }
}
check(/No free sessions left today/.test(await meter(page)), "after 3 sessions: " + await meter(page));
await lookUp(page, "milk");
await page.waitForFunction(() => document.querySelector("#dlg").open);
const dlg = await page.textContent("#dlg");
check(/used your 3 free sessions/.test(dlg) && /R 99 a month/.test(dlg), "4th session is blocked and the R99 upgrade is offered");
await shot(page, "7-limit");
let posted = "";
await ctx.route("https://sandbox.payfast.co.za/**", (route) => { posted = route.request().postData() || ""; route.fulfill({ status: 200, contentType: "text/html", body: "<h1>PayFast test page</h1>" }); });
await page.locator('#dlg [data-dlg="upgrade"]').click();
await page.waitForURL(/sandbox\.payfast\.co\.za/, { timeout: 10000 }).catch(() => {});
check(/amount=99\.00/.test(posted) && /subscription_type=1/.test(posted) && /signature=[0-9a-f]{32}/.test(posted), "Upgrade sends a signed R99 monthly subscription to PayFast (test mode)");

// 6. Account screen.
await page.goto(base + "/#account");
await page.waitForSelector(".plan");
const acct = await page.textContent("#main");
check(/Free plan/.test(acct) && /Used today: 3 of 3/.test(acct) && /Delete my PricePulse data/.test(acct), "account screen shows plan, usage and delete");
await shot(page, "8-account", true);

// 7. Under 18: liquor hidden.
const kidCtx = await browser.newContext({ viewport: { width: 412, height: 915 } });
const kid = await kidCtx.newPage();
await signIn(kid, "kid", MINOR);
check(!(await kid.textContent(".pills")).includes("Liquor"), "under 18: no Liquor category");
await kid.fill("#q", "castle");
await kid.waitForSelector(".empty");
check(/No products match/.test(await kid.textContent(".empty")), "under 18: liquor products cannot be found");
await kid.fill("#q", "milk");
await kid.locator("button.lookup").first().waitFor();
check(true, "under 18: groceries still work");
// Same ID on a second account is refused.
const dupCtx = await browser.newContext({ viewport: { width: 412, height: 915 } });
const dup = await dupCtx.newPage();
await signIn(dup, "second", null);
await dup.fill("#idform [name=idnum]", ADULT);
await dup.check("#idform [name=terms]");
await dup.click("#idform button.btn");
await dup.waitForFunction(() => document.querySelector(".err").textContent.length > 0);
check(/already linked to another/.test(await dup.textContent(".err")), "one ID number cannot open a second account");

// 8. Offline: saved list with a banner. (Use a fresh user with a list.)
const offCtx = await browser.newContext({ viewport: { width: 412, height: 915 } });
const off = await offCtx.newPage();
await signIn(off, "offline", makeId("750505"));
await lookUp(off, "milk");
await off.locator("article.card:has(.best)").first().waitFor();
await off.waitForFunction(() => navigator.serviceWorker && navigator.serviceWorker.controller, null, { timeout: 10000 }).catch(() => {});
await offCtx.route("**/api/**", (route) => route.abort("internetdisconnected"));
await off.reload();
await off.waitForSelector(".banner", { timeout: 15000 });
const banner = (await off.locator(".banner").allTextContents()).join(" | ");
check(/Showing saved prices from/.test(banner), "offline shows the saved list with a banner");
check(await off.locator(".li").count() === 1, "offline: saved list is readable");
await shot(off, "9-offline");

// 9. Dark theme, sizes, legal pages.
const dark = await browser.newContext({ viewport: { width: 412, height: 915 }, colorScheme: "dark", reducedMotion: "reduce" });
const dp = await dark.newPage();
await signIn(dp, "dark", makeId("700707"));
await lookUp(dp, "milk");
await dp.locator("article.card:has(.best)").first().waitFor();
check(await dp.evaluate(() => getComputedStyle(document.body).backgroundColor) === "rgb(21, 15, 16)", "dark theme follows the phone setting");
const sizes = await dp.evaluate(() => ({
  body: parseFloat(getComputedStyle(document.body).fontSize),
  price: parseFloat(getComputedStyle(document.querySelector(".price")).fontSize),
  smallest: Math.min(...[...document.querySelectorAll("button, select, input")].filter((b) => b.offsetParent && !b.closest(".vh"))
    .map((b) => Math.min(b.getBoundingClientRect().height, b.getBoundingClientRect().width))),
}));
check(sizes.body >= 17, "base font is at least 17px (" + sizes.body + ")");
check(sizes.price >= 28, "prices are at least 28px (" + sizes.price + ")");
check(sizes.smallest >= 48, "tap targets at least 48px (" + sizes.smallest.toFixed(1) + ")");
await shot(dp, "10-dark");
for (const p of ["terms.html", "privacy.html"]) {
  const r = await dp.goto(base + "/" + p);
  check(r.ok() && /R99|ID number/.test(await dp.textContent("main")), p + " loads");
}

check(errors.length === 0, "no JavaScript errors" + (errors.length ? ": " + errors.join("; ") : ""));
await browser.close();
console.log(failures ? `\n${failures} check(s) failed.` : "\nAll smoke checks passed.");
process.exit(failures ? 1 : 0);
