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
const meter = (page) => page.evaluate(() => meterText());   // the session count is no longer drawn in the header
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
check(!(await page.$("#meter")) && !(await page.$("#fresh")), "the session counter block is gone from the header");
// Three-dot menu on the sign-in screen: only what makes sense before sign-in.
await page.click("#menubtn");
check(await page.isVisible("#menupop"), "three-dot menu opens on the sign-in screen");
// The My account text must follow the real limits (a tester account has no item cap).
const acctText = await page.evaluate(() => {
  const base = { id_checked: true, adult: true, plan: "paid", sessions_per_day: null, sessions_used_today: 0, price_per_month: 99, subscription_status: "none", billing_ready: false };
  state.me = { ...base, session: { active: false, items_used: 0, items_max: 9999 } };
  const tester = viewAccount();
  state.me = { ...base, session: { active: false, items_used: 0, items_max: 45 } };
  const normal = viewAccount();
  state.me = null;
  return { tester, normal };
});
check(/unlimited items/.test(acctText.tester) && !/45 items each/.test(acctText.tester), "My account says unlimited items for a tester account");
check(/Unlimited lookup sessions, 45 items each/.test(acctText.normal) || /unlimited/i.test(acctText.normal), "My account text for a paid account is present");
check(!(await page.isVisible('[data-menu="list"]')), "menu hides My list before sign-in");
check(await page.isVisible("#menupop >> text=Privacy Policy") && await page.isVisible("#menupop >> text=Terms of Use"), "menu has Privacy Policy and Terms of Use");
const waHref = await page.getAttribute("#menuwa", "href");
check(/^https:\/\/wa\.me\/\?text=/.test(waHref) && decodeURIComponent(waHref).includes("pricepulse.trolleyup.com"), "WhatsApp share link carries the PricePulse address");
check(/App update \(v\d+\)/.test(await page.textContent("#menuupdate")), "menu shows the app version: " + (await page.textContent("#menuupdate")));
await page.keyboard.press("Escape");
check(!(await page.isVisible("#menupop")), "Escape closes the menu");
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
const headH = await page.$eval("header", (h) => h.getBoundingClientRect().height);
check(headH <= 200, "the top bar is compact (" + Math.round(headH) + "px tall)");
const rowsTop = await page.evaluate(() => ["base", "q"].map((id) => Math.round(document.getElementById(id).getBoundingClientRect().top)));
check(rowsTop[1] > rowsTop[0] + 20, "the search box is on a line by itself under Town");
const homeLink = await page.evaluate(() => { const a = document.querySelector("h1 a"); return a && a.getAttribute("href"); });
check(homeLink === "#find", "the PricePulse ZA title is a link to the home page");
check(await page.locator(".cat .circ svg").count() >= 4, "categories are soft circles with icons");
const look = await page.evaluate(() => {
  const css = (el, p) => getComputedStyle(el)[p];
  const r = (el) => el.getBoundingClientRect();
  const circ = r(document.querySelector(".cat .circ"));
  return { bodyBg: css(document.body, "backgroundColor"), top: css(document.querySelector(".apptop"), "backgroundColor"), nav: css(document.querySelector("nav.tabs"), "backgroundColor"),
    findbar: css(document.querySelector(".findbar"), "backgroundColor"), gap: r(document.getElementById("q")).top - r(document.getElementById("base")).bottom, circ: circ.width };
});
check(look.bodyBg === "rgb(211, 214, 218)", "the page is light charcoal (" + look.bodyBg + ")");
check(look.top === "rgb(176, 13, 39)" && look.nav === "rgb(176, 13, 39)", "the title bar and the footer menu are red");
check(look.findbar === "rgba(0, 0, 0, 0)", "the town and search blocks sit on the charcoal page, not on red");
check(look.gap >= 8, "the search block is detached from the town block (" + Math.round(look.gap) + "px gap)");
check(look.circ >= 46 && look.circ <= 50, "category circles are 15% smaller (" + Math.round(look.circ) + "px)");
check((await page.$eval("#q", (q) => q.getBoundingClientRect().width)) >= 110, "the search box is still wide enough to type in");
// The front screen shows no products: only how many have prices, and a prompt to search.
await page.waitForSelector(".homecard");
const homeText = await page.textContent(".homecard");
check(/\d/.test(await page.textContent(".homenum")) && /products with prices loaded/.test(homeText) && /Type what you are looking for/.test(homeText), "the front screen shows how many products have prices and asks the person to search");
check((await page.locator("#main article.card").count()) === 0, "no products are listed on the front screen until the person types");
check(/^Search \d+/.test((await page.getAttribute("#q", "placeholder")) || ""), "the search box says how many products it searches");
await shot(page, "3a-home");
await page.fill("#q", "milk");
await page.locator("button.lookup").first().waitFor();
check((await page.locator(".homecard").count()) === 0 && (await page.locator("#main article.card").count()) > 0, "products appear as soon as the person types");
await page.fill("#q", "");
await page.waitForSelector(".homecard");
check((await page.locator("#main article.card").count()) === 0, "clearing the search brings back the clean front screen");
await page.locator('button.cat[data-cat="Groceries"]').click();
await page.locator("button.lookup").first().waitFor();
check(true, "choosing a category lists its products");
await page.locator('button.cat[data-cat="All"]').click();
await page.waitForSelector(".homecard");

// Three-dot menu once signed in: every section, sharing and the version check.
await page.click("#menubtn");
await shot(page, "menu-open");
for (const [key, label] of [["find", "Find prices"], ["towns", "Town by town"], ["list", "My list"], ["account", "My account"], ["signout", "Sign out"]])
  check(await page.isVisible(`[data-menu="${key}"]`), "menu has " + label + " when signed in");
const heights = await page.$$eval("#menupop .mi", (els) => els.filter((e) => e.offsetParent).map((e) => e.getBoundingClientRect().height));
check(heights.every((h) => h >= 48), "every menu row is at least 48px tall (smallest " + Math.min(...heights) + ")");
// A stacked drop-down on the right edge of the screen, tight rows, no scrolling on an ordinary phone.
await page.setViewportSize({ width: 360, height: 780 });
await page.waitForTimeout(150);                                   // turning the phone closes the menu, so open it again
if (!(await page.isVisible("#menupop"))) await page.click("#menubtn");
const fit = await page.$eval("#menupop", (m) => { const r = m.getBoundingClientRect(); const rows = [...m.querySelectorAll(".mi")].filter((e) => e.offsetParent).map((e) => e.getBoundingClientRect()); return { scroll: m.scrollHeight, shown: m.clientHeight, bottom: r.bottom, right: r.right, win: window.innerHeight, winW: window.innerWidth, stacked: rows.every((q, i) => i === 0 || q.top >= rows[i - 1].bottom - 1), gap: Math.max(...rows.slice(1).map((q, i) => q.top - rows[i].bottom)) }; });
check(fit.stacked, "menu rows are stacked one under the other");
check(fit.winW - fit.right <= 12, "menu drops down on the right edge of the screen (" + (fit.winW - fit.right) + "px from the edge)");
const lastOk = await page.evaluate(() => { const rows = [...document.querySelectorAll("#menupop .mi")].filter((e) => e.offsetParent); const r = rows[rows.length - 1].getBoundingClientRect(); const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return !!hit && hit.closest(".mi") === rows[rows.length - 1]; });
check(lastOk, "the last menu row (App update) is not hidden behind the bottom tabs");
check(fit.scroll <= fit.shown && fit.bottom <= fit.win, "menu fits a 360x780 phone with no scrolling (" + fit.scroll + "px of " + fit.win + "px)");
check(fit.gap <= 8, "no extra space between menu rows (largest gap " + fit.gap + "px)");
await shot(page, "menu-stacked");
await page.setViewportSize({ width: 360, height: 600 });
await page.waitForTimeout(150);
check(!(await page.isVisible("#menupop")), "turning the phone (resize) closes the menu");
await page.click("#menubtn");
const small = await page.$eval("#menupop", (m) => ({ bottom: m.getBoundingClientRect().bottom, win: window.innerHeight, inner: m.scrollHeight > m.clientHeight }));
check(small.bottom <= small.win && small.inner, "on a short screen the menu stays on screen and scrolls inside itself");
await page.setViewportSize({ width: 412, height: 915 });
await page.waitForTimeout(150);
if (!(await page.isVisible("#menupop"))) await page.click("#menubtn");
// How it works: a chat-style guide.
await page.click('[data-menu="how"]');
await page.waitForSelector("#howdlg[open]");
const how = await page.textContent("#howdlg");
check(/Look up price/.test(how) && /Send to TrolleyUp/.test(how) && /R99 a month/.test(how), "How it works explains looking up, sending to TrolleyUp and the plan");
check(await page.locator("#howdlg .bub.me").count() >= 2, "How it works is laid out as a chat with your questions on the right");
await shot(page, "how-it-works");
await page.click('#howdlg [data-how="close"]');
check(!(await page.isVisible("#howdlg")), "How it works closes with its back button");
await page.click("#menubtn");
await page.click('[data-menu="list"]');
await page.waitForFunction(() => document.querySelector("nav.tabs [aria-current=page]").textContent.includes("List"));
check(!(await page.isVisible("#menupop")), "choosing a menu item goes there and closes the menu");
await page.click("#menubtn");
await page.click('[data-menu="update"]');
await page.click('[data-dlg="doupdate"]');
await page.waitForFunction(() => /newest version|Could not/.test(document.getElementById("dlg").textContent));
check(/newest version \(\d+\)/.test(await page.textContent("#dlg")), "App update says the newest version when up to date");
await page.click('#dlg [data-dlg=""]');
await page.click("#menubtn");
await page.click('[data-menu="colours"]');
await page.click('[data-dlg="theme-dark"]');
check((await page.getAttribute("html", "data-theme")) === "dark", "Colours choice from the menu switches to dark");
await page.click("#menubtn");
await page.click('[data-menu="colours"]');
await page.click('[data-dlg="theme-system"]');
await page.click('[data-menu="find"]', { force: true }).catch(() => {});
await page.goto(base + "/#find");
await page.waitForSelector(".pills");

// 2. Look up a price.
await page.fill("#q", "milk");
await page.locator("button.lookup").first().waitFor();
check(!/R \d/.test(await page.textContent(".grid")), "no prices shown until an item is looked up");
await shot(page, "3-find");
await page.locator("button.lookup").first().click();
const cardEl = page.locator("article.card:has(.best)").first();
await cardEl.waitFor();
check((await cardEl.locator("h3").evaluate((h) => getComputedStyle(h).color)) === "rgb(63, 55, 56)", "item names are 15% lighter than before (rgb 63,55,56)");
check(await cardEl.locator(".badge").count() >= 3, "looked-up milk shows at least 3 stores");
check((await cardEl.textContent()).includes("Best price"), "best price is labelled in text, not only colour");
check(/Session 1 of 3 today · 1 of 45 items/.test(await meter(page)), "meter after lookup: " + await meter(page));
await shot(page, "4-looked-up");
const badges = await page.evaluate(() => ({
  withUrl: badgeLink("Checkers", { url: "https://www.checkers.co.za/p/1" }),
  without: badgeLink("Checkers", { url: "" }),
}));
check(/<a class="badge blink" href="https:\/\/www\.checkers\.co\.za\/p\/1"/.test(badges.withUrl) && /opens a new tab/.test(badges.withUrl) && /noopener/.test(badges.withUrl), "store label links to the store's product page");
check(/^<span class="badge"/.test(badges.without), "store label is plain text when there is no product link");
await page.getByRole("button", { name: "Request an item" }).first().click();
await page.fill("#reqterm", "white mushrooms 250g");
await page.getByRole("button", { name: "Send request" }).click();
await page.getByText("Request sent").first().waitFor();
check(true, "an item can be requested");
await page.locator("#dlg").getByRole("button", { name: "OK" }).click();

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
check(await page.getByRole("button", { name: /^Send to TrolleyUp/ }).count() === 0, "nothing is sent until items are picked");
await page.locator("button.tupick").first().click();
check((await page.locator("button.tupick[aria-pressed=true]").count()) === 1, "the TrolleyUp icon picks an item");
await page.getByRole("button", { name: /^Send to TrolleyUp/ }).click();
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

// 10. Admin area: hidden for everyone, then switched on for one account by hand.
check(!(await page.evaluate(() => { const b = document.querySelector('#menupop [data-menu="admin"]'); return b && b.offsetParent !== null; })), "Admin area is not in the menu for an ordinary account");
{
  const { execFileSync } = require("child_process");
  const admCtx = await browser.newContext({ viewport: { width: 412, height: 915 } });
  const adm = await admCtx.newPage();
  const admErrors = [];
  adm.on("pageerror", (e) => admErrors.push(e.message));
  await signIn(adm, "admin", makeId("650505"));
  await adm.click("#menubtn");
  check(!(await adm.isVisible('#menupop [data-menu="admin"]')), "Admin area is hidden until the account is made an admin");
  execFileSync(process.env.PP_PYTHON || "python3", ["admin.py", "admin-role", "dev-admin", "on"], { cwd: process.env.PP_SMOKE_BACKEND, env: process.env });
  await adm.reload();
  await adm.waitForSelector(".pills");
  await adm.click("#menubtn");
  check(await adm.isVisible('#menupop [data-menu="admin"]'), "Admin area appears in the three-dot menu for an admin");
  await adm.click('#menupop [data-menu="admin"]');
  await adm.waitForSelector(".admgrid");
  check(/accounts/.test(await adm.textContent(".admgrid")), "Admin overview shows the counts");
  await adm.click('[data-adm="tab"][data-tab="items"]');
  await adm.waitForSelector(".admtable tbody tr");
  const total = await adm.locator(".admtable tbody tr").count();
  check(total > 10, "Admin items list shows every item, one line each (" + total + " lines)");
  await adm.selectOption("#admstore", "Checkers");
  await adm.waitForFunction((n) => document.querySelectorAll(".admtable tbody tr").length < n, total);
  const onlyChk = await adm.locator(".admtable tbody tr td:first-child").allTextContents();
  check(onlyChk.length > 0 && onlyChk.every((t) => t === "Checkers"), "Admin items can be limited to one store");
  const rects = await adm.evaluate(() => ["admstore", "admcat", "admq"].map((id) => { const r = document.getElementById(id).getBoundingClientRect(); return { top: Math.round(r.top), w: r.width }; }));
  check(rects[2].top > rects[0].top + 20 && rects[2].top > rects[1].top + 20 && rects[2].w > 300, "Admin search box is on a line by itself, full width (" + Math.round(rects[2].w) + "px)");
  const cats = await adm.locator("#admcat option").allTextContents();
  check(cats.length > 2, "Admin items can be limited by type: " + cats.join(", "));
  await adm.selectOption("#admcat", cats[1]);
  await adm.waitForTimeout(800);
  check(await adm.locator(".admtable tbody tr").count() > 0, "Store and type filters work together");
  await shot(adm, "11-admin-items");
  // Hide one product from shoppers, see it under "Hidden items", show it again.
  const firstName = (await adm.locator(".admtable tbody tr td:nth-child(2)").first().textContent()).trim();
  await adm.click('[data-adm="vis"][data-state="hide"] >> nth=0');
  await adm.waitForFunction(() => /Hidden/.test(document.querySelector(".admtable tbody tr").textContent));
  await adm.selectOption("#admshown", "no");
  await adm.waitForFunction(() => document.querySelectorAll(".admtable tbody tr").length === 1);
  check(/Hidden/.test(await adm.textContent(".admtable tbody")) && (await adm.locator('[data-adm="vis"][data-state="show"]').count()) === 1, "an item can be hidden from shoppers and then found under Hidden items");
  await adm.click('[data-adm="vis"][data-state="show"]');
  await adm.waitForFunction(() => /No items match/.test(document.querySelector(".admtable tbody").textContent));
  await adm.selectOption("#admshown", "");
  await adm.waitForFunction(() => document.querySelectorAll(".admtable tbody tr").length > 0 && !/No items match/.test(document.querySelector(".admtable tbody").textContent));
  check(firstName.length > 0, "the item is shown to shoppers again");
  await adm.evaluate(() => { window.__printed = 0; window.print = () => { window.__printed++; }; });
  await adm.click('[data-adm="preview"]');
  await adm.waitForSelector('[data-adm="doprint"]');
  check(/Print preview/.test(await adm.textContent("#main")), "Print this list opens a print preview first");
  await adm.click('[data-adm="doprint"]');
  await adm.waitForFunction(() => window.__printed === 1);
  check(true, "the Print button in the preview prints");
  // What the printer gets: only the list. (The old rule hid the whole page, so nothing printed.)
  await adm.emulateMedia({ media: "print" });
  const pr = await adm.evaluate(() => {
    const vis = (el) => !!el && getComputedStyle(el).display !== "none" && el.getBoundingClientRect().height > 0;
    return { header: vis(document.querySelector("header")), nav: vis(document.querySelector("nav.tabs")), buttons: vis(document.querySelector('[data-adm="doprint"]')),
      table: vis(document.querySelector(".admtable")), rows: document.querySelectorAll(".admtable tbody tr").length, title: vis(document.querySelector(".printtitle")) };
  });
  check(!pr.header && !pr.nav && !pr.buttons && pr.table && pr.title && pr.rows > 0, "printed page shows the title and the table only (" + JSON.stringify(pr) + ")");
  const pdf = await adm.pdf({ format: "A4" });
  check(pdf.length > 3000, "a PDF of the list can be made (" + pdf.length + " bytes)");
  await adm.emulateMedia({ media: "screen" });
  await adm.click('[data-adm="back"]');
  await adm.waitForSelector('[data-adm="preview"]');
  check(!(await adm.evaluate(() => document.body.classList.contains("adm-print"))), "Back to the list leaves print mode");
  const sm = await adm.evaluate(() => Math.min(...[...document.querySelectorAll("button, select, input")].filter((b) => b.offsetParent).map((b) => b.getBoundingClientRect().height)));
  check(sm >= 47.5, "Admin controls are at least 48px tall (" + sm.toFixed(1) + ")");
  await adm.click('[data-adm="tab"][data-tab="users"]');
  await adm.waitForSelector('select[data-admf="plan"]');
  check(/dev-admin/.test(await adm.textContent(".admtable")), "Admin accounts tab lists accounts by ID");
  await adm.selectOption('select[data-admf="plan"] >> nth=0', "free");
  await adm.waitForSelector("#dlg[open]");
  check(/Change plan/.test(await adm.textContent("#dlg")), "Changing a plan asks first");
  await adm.click('#dlg button:has-text("Cancel")');
  await adm.click('[data-adm="tab"][data-tab="requests"]');
  await adm.waitForSelector(".admtable");
  check(/Item asked for/.test(await adm.textContent("#main")), "Admin requests tab opens");
  // Local stores: add a store, check a weekly file first, load it, see it listed.
  await adm.click('[data-adm="tab"][data-tab="localstores"]');
  await adm.waitForSelector('[data-adm="lsnew"]');
  check(/replaces/.test(await adm.textContent("#main")), "Local stores tab explains that each file replaces the list");
  await adm.click('[data-adm="lsnew"]');
  await adm.waitForSelector("#lsname");
  await adm.fill("#lsname", "Paarl Corner Cafe");
  await adm.selectOption("#lsregion", "paarl");
  await adm.fill("#lscontact", "Tannie Sanet");
  await adm.click('#dlg button:has-text("Save")');
  await adm.waitForSelector(".admtable tbody tr b");
  check(/Paarl Corner Cafe/.test(await adm.textContent(".admtable")), "a local store can be added");
  const lsCsv = "item,price,size,sku,department,category\r\nBoerewors,89.90,1kg,BW1,Butchery,Fresh\r\nTannie Rusks,45,500g,RK1,Bakery,Groceries\r\nBad Row,0,,,,\r\n";
  const [chooser] = await Promise.all([adm.waitForEvent("filechooser"), adm.click('[data-adm="lsimport"]')]);
  await chooser.setFiles({ name: "week1.csv", mimeType: "text/csv", buffer: Buffer.from(lsCsv, "utf8") });
  await adm.waitForSelector("#dlg[open] #lsreplace");
  const lsPrev = await adm.textContent("#dlg");
  check(/3 rows/.test(lsPrev) && /2<\/b> new items|2 new items/.test(lsPrev.replace(/\s+/g, " ")) && /1 rows skipped|1 row/.test(lsPrev.replace(/\s+/g, " ")), "the weekly file is checked first: " + lsPrev.replace(/\s+/g, " ").slice(0, 120));
  await adm.click('#dlg button:has-text("Load the file")');
  await adm.waitForFunction(() => /File loaded/.test(document.querySelector("#dlg").textContent));
  await adm.click('#dlg button:has-text("OK")');
  await adm.waitForFunction(() => /\b2\b/.test((document.querySelector(".admtable tbody tr td.num") || {}).textContent || ""));
  check(true, "the store's items are listed after loading");
  await adm.click('[data-adm="lshistory"]');
  await adm.waitForFunction(() => /week1\.csv/.test(document.querySelector("#dlg").textContent));
  check(true, "file history shows the weekly file");
  await adm.click('#dlg button:has-text("Close")');
  await adm.setViewportSize({ width: 1440, height: 900 });
  await adm.waitForTimeout(300);
  const wide = await adm.evaluate(() => ({ main: Math.round(document.querySelector("main").getBoundingClientRect().width), find: getComputedStyle(document.querySelector("header .findbar")).display }));
  check(wide.main > 1000 && wide.find === "none", "on a desktop screen the admin area uses the full width and drops the shopper search bar (" + wide.main + "px)");
  await shot(adm, "12-admin-desktop-stores");
  await adm.setViewportSize({ width: 412, height: 915 });
  await adm.click('[data-adm="tab"][data-tab="essentials"]');
  await adm.waitForSelector('[data-adm="espick"]');
  const csvText = "\uFEFFItem Name;Category\r\nWhite mushrooms 250g;Fresh\r\n\"Koo baked beans, 410g\";Groceries\r\nPanado tablets 24s;Health & Beauty\r\nx;Fresh\r\n";
  await adm.setInputFiles("#esfile", { name: "essentials.csv", mimeType: "text/csv", buffer: Buffer.from(csvText, "utf8") });
  await adm.waitForSelector("#dlg[open]");
  check(/4 items/.test(await adm.textContent("#dlg")) && /White mushrooms/.test(await adm.textContent("#dlg")), "a CSV file (semicolons, quotes, BOM) is read and previewed");
  await adm.click('#dlg button:has-text("Add 4 items")');
  await adm.waitForFunction(() => /Import finished/.test(document.querySelector("#dlg").textContent));
  const done = await adm.textContent("#dlg");
  check(/3 added/.test(done) && /1 skipped/.test(done), "import adds the good rows and reports the bad one: " + done.replace(/\s+/g, " ").slice(0, 90));
  await adm.click('#dlg button:has-text("OK")');
  await adm.waitForFunction(() => document.querySelectorAll(".admtable tbody tr").length === 3);
  check(/Koo baked beans, 410g/.test(await adm.textContent(".admtable")), "the essential items table lists what was imported");
  await adm.setInputFiles("#esfile", { name: "again.csv", mimeType: "text/csv", buffer: Buffer.from("item\r\nwhite mushrooms 250g\r\n", "utf8") });
  await adm.waitForSelector("#dlg[open]");
  await adm.click('#dlg button:has-text("Add 1 items")');
  await adm.waitForFunction(() => /0 added/.test(document.querySelector("#dlg").textContent));
  check(true, "importing the same item again changes nothing");
  await adm.click('#dlg button:has-text("OK")');
  await adm.click('[data-adm="esremove"] >> nth=0');
  await adm.click('#dlg button:has-text("Remove it")');
  await adm.waitForFunction(() => document.querySelectorAll(".admtable tbody tr").length === 2);
  check(true, "an essential item can be removed");
  await adm.click('[data-adm="tab"][data-tab="settings"]');
  await adm.waitForSelector(".admtable code");
  check(/PP_FREE_SESSIONS/.test(await adm.textContent("#main")), "Admin settings tab lists the settings");
  check(admErrors.length === 0, "no JavaScript errors in the admin area" + (admErrors.length ? ": " + admErrors.join("; ") : ""));
}

check(errors.length === 0, "no JavaScript errors" + (errors.length ? ": " + errors.join("; ") : ""));
await browser.close();
console.log(failures ? `\n${failures} check(s) failed.` : "\nAll smoke checks passed.");
process.exit(failures ? 1 : 0);
