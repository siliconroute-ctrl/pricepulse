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
  if (id) { await page.fill("#idform [name=idnum]", id); await page.check("#idform [name=terms]"); await page.click("#idform button.btn"); await page.waitForSelector(".cats8"); }
}
async function lookUp(page, word) {
  await page.fill("#q", word);
  const btn = page.locator(".grid button.lookup").first();
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
{
  const t = await page.textContent("#main");
  check(t.includes("29 lookup sessions a day") && t.includes("R39 a month") && !t.includes("R99") && !/unlimited/i.test(t), "sign-in screen explains 29 sessions a day and the R39 TrolleyUp plan");
}
check(!(await page.isVisible("nav.tabs")), "no tabs or prices before sign-in");
await shot(page, "1-signin");
check(!(await page.$("#meter")) && !(await page.$("#fresh")), "the session counter block is gone from the header");
// Three-dot menu on the sign-in screen: only what makes sense before sign-in.
await page.click("#menubtn");
check(await page.isVisible("#menupop"), "three-dot menu opens on the sign-in screen");
// The My account text must follow the real limits (a tester account has no item cap).
const acctText = await page.evaluate(() => {
  const base = { id_checked: true, adult: true, plan: "paid", sessions_per_day: null, sessions_used_today: 0, price_per_month: 39, subscription_status: "none", billing_ready: false };
  state.me = { ...base, session: { active: false, items_used: 0, items_max: 9999 } };
  const tester = viewAccount();
  state.me = { ...base, sessions_per_day: 29, session: { active: false, items_used: 0, items_max: 45 } };
  const normal = viewAccount();
  state.me = null;
  return { tester, normal };
});
check(/Tester account: no limits/.test(acctText.tester) && !/45 items each/.test(acctText.tester), "My account says no limits for a tester account");
check(/29 lookup sessions a day, 45 items each/.test(acctText.normal) && /TrolleyUp plan/.test(acctText.normal) && !/nlimited/.test(acctText.normal), "My account shows the same 29 and 45 limits for a paid account, named TrolleyUp plan");
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
await page.waitForSelector(".cats8");
check(/29 lookup sessions left today/.test(await meter(page)), "meter before first lookup: " + await meter(page));
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
check(look.bodyBg === "rgb(247, 244, 238)", "the page has a soft WhatsApp-style light background (" + look.bodyBg + ")");
check(look.top === "rgb(247, 244, 238)" && look.nav === "rgb(247, 244, 238)", "the title bar and the footer menu have the same soft background as the page");
check(look.findbar === "rgb(247, 244, 238)", "the town chip and the search box sit in the same soft background");
check(look.gap >= 8, "the search box has a gap under the town chip (" + Math.round(look.gap) + "px)");
check(look.circ >= 52 && look.circ <= 60, "category circles are about 56px (" + Math.round(look.circ) + "px)");
check((await page.$eval("#q", (q) => q.getBoundingClientRect().width)) >= 110, "the search box is still wide enough to type in");
// Front screen: message, three shortcut buttons, SHOP BY CATEGORY, notice and the promo panel. No products.
await page.waitForSelector(".cats8");
check((await page.locator(".homecard, .homehead").count()) === 0 && !/Local shops\. Big-chain prices|Type what you need/.test(await page.textContent("#main")), "there is no message bar on the front screen");
check(!/products with prices loaded|\d+ products/.test(await page.textContent("#main")), "the front screen does not show a product count");
check((await page.locator("#main article.card").count()) === 0 && (await page.locator("#main .hscroll").count()) === 0 && (await page.locator("#main .pimg").count()) === 0, "the first screen displays no products");
check(/^Search for a product/.test((await page.getAttribute("#q", "placeholder")) || ""), "the search box invites a search without a count");
const heading = await page.$eval("#main .sect h2", (h) => ({ text: h.textContent.trim(), tt: getComputedStyle(h).textTransform }));
check(/^shop by category$/i.test(heading.text) && heading.tt === "uppercase" && !/Aisles|Quick shops/i.test(await page.textContent("#main")), "the category section is headed SHOP BY CATEGORY");
const labels = await page.$$eval(".cats8 .cat .lbl", (els) => els.map((e) => e.textContent.trim()));
check(JSON.stringify(labels) === JSON.stringify(["All", "Groceries", "Fresh", "Liquor", "Health & Beauty", "Pets", "Stationery", "Electronics"]), "eight categories in the required order: " + labels.join(", "));
const catTops = await page.$$eval(".cats8 .cat", (els) => els.map((e) => Math.round(e.getBoundingClientRect().top)));
check(new Set(catTops).size === 2 && catTops.filter((y) => y === catTops[0]).length === 4, "the categories sit in 2 rows of 4");
check((await page.locator(".cats8 .cat .circ svg").count()) === 8 && (await page.locator(".cats8 .cat[data-cat] .circ").count()) === 8, "every category has its own outline icon in a coloured circle");
const circ0 = await page.$eval(".cats8 .cat .circ", (e) => Math.round(e.getBoundingClientRect().width));
check(circ0 >= 52 && circ0 <= 60, "the category circles are about 56px (" + circ0 + "px)");
const promoH = await page.evaluate(() => { const e = document.querySelector("#main .promo"), n = document.querySelector("nav.tabs"); if (!e) return null; const r = e.getBoundingClientRect(); return { h: Math.round(r.height), vh: window.innerHeight, gap: Math.round(n.getBoundingClientRect().top - r.bottom) }; });
check(promoH && Math.abs(promoH.h - promoH.vh * 0.25) <= 3, "the photo panel takes 25% of the page height (" + (promoH ? promoH.h + "px of " + promoH.vh + "px" : "missing") + ")");
check(promoH && Math.abs(promoH.gap) <= 1, "the photo panel starts right at the footer, with no empty space under it (gap " + (promoH ? promoH.gap : "?") + "px)");
check((await page.locator("#main .banner").count()) === 0, "the front screen has no warning cards");
check((await page.locator("#main .notice").count()) === 0 && !/Prices may differ in store/.test(await page.textContent("#main")), "the front screen has no pricing notice bar");
const promo = await page.evaluate(async () => {
  const el = document.querySelector("#main .promo");
  if (!el) return null;
  const img = el.querySelector("img");
  if (img && !img.complete) await new Promise((r) => { img.onload = r; img.onerror = r; setTimeout(r, 3000); });
  return { title: el.querySelector("h2").textContent.trim(), items: [...el.querySelectorAll("li")].map((l) => l.textContent.trim()), img: img ? img.naturalWidth : 0 };
});
check(promo && promo.title === "Compare Prices Before You Shop" && promo.items.length === 0 && promo.img > 0, "the promo panel shows its title over the photo, no tick lines");
await shot(page, "3a-home");
// The body scrolls; the header and the bottom tabs stay fixed.
await page.setViewportSize({ width: 360, height: 640 });
await page.waitForTimeout(250);
await page.setViewportSize({ width: 360, height: 780 });
await page.waitForTimeout(300);
await page.evaluate(() => window.scrollTo(0, 0));
const gap780 = await page.evaluate(() => { const e = document.querySelector("#main .promo"), n = document.querySelector("nav.tabs"); return { gap: Math.round(n.getBoundingClientRect().top - e.getBoundingClientRect().bottom), h: Math.round(e.getBoundingClientRect().height) }; });
check(gap780.h >= 180 && gap780.h <= 210, "360x780: the photo panel is about 25% of the screen (" + gap780.h + "px)");
await page.setViewportSize({ width: 360, height: 640 });
await page.waitForTimeout(250);
const promo360 = await page.$eval("#main .promo", (e) => ({ h: Math.round(e.getBoundingClientRect().height), vh: window.innerHeight }));
check(Math.abs(promo360.h - promo360.vh * 0.25) <= 3, "on a short 360x640 screen the photo panel is still 25% of the page height (" + promo360.h + "px of " + promo360.vh + "px)");
await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
await page.waitForTimeout(250);
const fixedRects = await page.evaluate(() => ({ head: Math.round(document.querySelector("header").getBoundingClientRect().top), nav: Math.round(document.querySelector("nav.tabs").getBoundingClientRect().bottom), win: window.innerHeight, scrolled: window.scrollY > 0, wide: document.documentElement.scrollWidth > window.innerWidth + 1 }));
check(fixedRects.head >= -1 && fixedRects.head <= 1 && fixedRects.nav === fixedRects.win && !fixedRects.wide, "the body scrolls while the header and the bottom tabs stay in place (header top " + fixedRects.head + ", tabs bottom " + fixedRects.nav + " of " + fixedRects.win + ")");
await shot(page, "3b-home-scrolled");
await page.evaluate(() => window.scrollTo(0, 0));
await page.setViewportSize({ width: 412, height: 915 });
await page.waitForTimeout(200);
check((await page.locator(".fronttile, .fronttiles").count()) === 0 && !/All Stores|Local Stores/.test(await page.textContent("#main")), "the All Stores, Local Stores and Specials buttons are gone from the front screen");
// Whole tile is the button: click the label text of a new category; it has no products yet, so the normal empty screen shows.
await page.locator('.cats8 .cat[data-cat="Pets"] .lbl').click();
await page.waitForSelector("#main .empty");
check(/No products match/.test(await page.textContent("#main")) && (await page.locator('.cats8 .cat[data-cat="Pets"][aria-pressed="true"]').count()) === 1, "Pets opens the normal empty screen (no products carry that category yet) and shows as selected");
await page.locator('.cats8 .cat[data-cat="Stationery"]').click();
await page.waitForSelector('.cats8 .cat[data-cat="Stationery"][aria-pressed="true"]');
await page.locator('.cats8 .cat[data-cat="Electronics"]').click();
await page.waitForSelector('.cats8.sub3 [data-fsub]');
check((await page.locator('.cats8.sub3 [data-fsub]').count()) === 6, "Electronics opens its own sub-category icon screen");
await page.locator('[data-act="clearcat"]').click();
await page.waitForSelector("#main .promo");
check(errors.length === 0, "the new categories open without JavaScript errors");
check(true, "All clears the category filter");
await page.fill("#q", "milk");
await page.locator(".grid button.lookup").first().waitFor();
check((await page.locator("#main .promo").count()) === 0 && (await page.locator("#main .grid article.card").count()) > 0, "products appear as soon as the person types");
await page.fill("#q", "");
await page.waitForSelector("#main .promo");
check((await page.locator("#main .grid article.card").count()) === 0, "clearing the search brings back the clean front screen");
await page.locator('button.cat[data-cat="Groceries"]').click();
await page.locator(".grid button.lookup").first().waitFor();
check(true, "choosing a category lists its products");
await page.locator('button.cat[data-cat="All"]').click();
await page.waitForSelector(".cats8");
// The Find tab and the title go to the clean front screen from a results list, a category and a filter.
await page.fill("#q", "milk");
await page.locator(".grid button.lookup").first().waitFor();
await page.locator('nav.tabs button[data-view="find"]').click();
await page.waitForSelector(".cats8");
check((await page.inputValue("#q")) === "" && (await page.locator("#main .grid article.card").count()) === 0, "the Find tab returns from a search to the clean front screen");
await page.locator('button.cat[data-cat="Groceries"]').click();
await page.locator(".grid button.lookup").first().waitFor();
await page.locator("a.homelink").click();
await page.waitForSelector(".cats8");
check((await page.locator('.cats8 .cat[aria-pressed="true"][data-cat="All"]').count()) === 1, "the PricePulse title returns from a category to the front screen with All selected");

// Three-dot menu once signed in: every section, sharing and the version check.
await page.setViewportSize({ width: 360, height: 695 });
await page.click("#menubtn");
const fitAdmin = await page.evaluate(() => { const p = document.getElementById("menupop"), had = p.getAttribute("data-admin"); p.setAttribute("data-admin", "1"); const r = { sh: p.scrollHeight, ch: p.clientHeight }; if (had === null) p.removeAttribute("data-admin"); else p.setAttribute("data-admin", had); return r; });
check(fitAdmin.sh <= fitAdmin.ch + 1, "the whole three-dot menu (admin row included) fits on a 360x695 phone without scrolling (" + fitAdmin.sh + " of " + fitAdmin.ch + "px)");
await page.click("#menubtn");
await page.setViewportSize({ width: 412, height: 915 });
await page.click("#menubtn");
await shot(page, "menu-open");
for (const [key, label] of [["how", "How it works"], ["colours", "Colours"], ["signout", "Sign out"], ["update", "App update"]])
  check(await page.isVisible(`[data-menu="${key}"]`), "menu has " + label + " when signed in");
const heights = await page.$$eval("#menupop .mi", (els) => els.filter((e) => e.offsetParent).map((e) => e.getBoundingClientRect().height));
check(heights.every((h) => h >= 44), "every menu row is at least 44px tall (smallest " + Math.min(...heights) + ")");
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
await page.setViewportSize({ width: 360, height: 400 });
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
check(/Look up price/.test(how) && /Send to TrolleyUp/.test(how) && /R39 a month/.test(how) && /29 lookup sessions/.test(how), "How it works explains looking up, sending to TrolleyUp and the R39 plan");
check(await page.locator("#howdlg .bub.me").count() >= 2, "How it works is laid out as a chat with your questions on the right");
await shot(page, "how-it-works");
await page.click('#howdlg [data-how="close"]');
check(!(await page.isVisible("#howdlg")), "How it works closes with its back button");
await page.click("#menubtn");
await page.click('[data-menu="how"]');
await page.waitForSelector("#howdlg", { state: "visible" });
check(!(await page.isVisible("#menupop")), "choosing a menu item opens it and closes the menu");
await page.click('#howdlg [data-how="close"]');
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
await page.waitForSelector(".cats8");

// 2. Look up a price.
await page.fill("#q", "milk");
await page.locator(".grid button.lookup").first().waitFor();
check(!/R \d/.test(await page.textContent(".grid")), "no prices shown until an item is looked up");
await shot(page, "3-find");
await page.locator(".grid button.lookup").first().click();
const cardEl = page.locator("article.card:has(.best)").first();
await cardEl.waitFor();
check((await cardEl.locator("h3").evaluate((h) => getComputedStyle(h).color)) === "rgb(0, 0, 0)", "item names are crisp black");
check((await cardEl.locator(".sbtn").count()) >= 2 && /Best price/.test(await cardEl.textContent()) && /Next:/.test(await cardEl.textContent()), "looked-up milk shows the best store and the next store as two store buttons on the row");
const sb = await cardEl.locator(".sbtn").evaluateAll((els) => els.map((e) => ({ h: Math.round(e.getBoundingClientRect().height), a: e.tagName === "A", blank: e.getAttribute("target") === "_blank", safe: /noopener/.test(e.getAttribute("rel") || ""), label: e.getAttribute("aria-label") || "" })));
check(sb.every((x) => x.h >= 44), "the store buttons are at least 44px tall (" + sb.map((x) => x.h).join(", ") + ")");
check(sb.filter((x) => x.a).every((x) => x.blank && x.safe && /opens a new tab/.test(x.label)), "store buttons that link open the store's page in a new tab, safely (" + sb.filter((x) => x.a).length + " links)");
const vp = page.viewportSize();
const rowH = await cardEl.evaluate((e) => Math.round(e.getBoundingClientRect().height));
const smallH = await page.locator("article.prow.small").first().evaluate((e) => Math.round(e.getBoundingClientRect().height)).catch(() => 0);
console.log("INFO row heights at " + vp.width + "px: looked-up " + rowH + "px, not looked up " + smallH + "px");
check(rowH <= 240 && (smallH === 0 || smallH <= 130), "product rows are compact (looked-up " + rowH + "px, not looked up " + smallH + "px)");
check((await page.locator(".grid").evaluate((g) => getComputedStyle(g).display)) === "flex", "results are one column of rows");
check((await page.locator(".grid .pimg, .grid .pph").count()) === 0, "listings show no picture and no picture icon for grocery items");
await page.setViewportSize({ width: 360, height: 780 });
await page.waitForTimeout(250);
const rowH360 = await cardEl.evaluate((e) => Math.round(e.getBoundingClientRect().height));
const overflow360 = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
console.log("INFO row height at 360px: " + rowH360 + "px");
check(rowH360 <= 260 && !overflow360, "on a 360px phone a looked-up row is " + rowH360 + "px tall and nothing scrolls sideways");
await page.setViewportSize({ width: vp.width, height: vp.height });
check((await cardEl.textContent()).includes("Best price"), "best price is labelled in text, not only colour");
check(/Session 1 of 29 today · 1 of 45 items/.test(await meter(page)), "meter after lookup: " + await meter(page));
await shot(page, "4-looked-up");
const rowHtml = await page.evaluate(() => {
  const mk = (url) => ({ price: 10, promo: null, url, at: new Date().toISOString(), stale: false, local: false, unit: null });
  return card({ p: { id: 9001, name: "Test milk", size: "1L", cat: "Groceries", image: "", prices: { Checkers: mk("https://www.checkers.co.za/p/1"), "Pick n Pay": { ...mk("https://www.pnp.co.za/p/2"), price: 12 }, Woolworths: { ...mk(""), price: 13 } } }, on: false, q: 1 });
});
check((rowHtml.match(/<a class="sbtn[^"]*" href="https:\/\/www\.checkers\.co\.za\/p\/1" target="_blank" rel="noopener noreferrer"/g) || []).length === 1 && (rowHtml.match(/<a class="sbtn[^"]*" href="https:\/\/www\.pnp\.co\.za\/p\/2" target="_blank" rel="noopener noreferrer"/g) || []).length === 1, "on a row both the best store and the next store are links to their own product pages");
check(/Open this product on the Checkers website \(opens a new tab\)/.test(rowHtml) && /Open this product on the Pick n Pay website/.test(rowHtml), "the row store links say where they go");
const noLink = await page.evaluate(() => { const mk = (url) => ({ price: 10, promo: null, url, at: new Date().toISOString(), stale: false, local: false, unit: null }); return card({ p: { id: 9002, name: "Plain", size: "1L", cat: "Groceries", image: "", prices: { Checkers: mk(""), "Pick n Pay": mk("") } }, on: false, q: 1 }); });
check(!/<a class="sbtn/.test(noLink) && (noLink.match(/<span class="sbtn/g) || []).length === 2, "a store without a product link is a plain label of the same size");
const badges = await page.evaluate(() => ({
  withUrl: badgeLink("Checkers", { url: "https://www.checkers.co.za/p/1" }),
  without: badgeLink("Checkers", { url: "" }),
}));
check(/<a class="badge blink" href="https:\/\/www\.checkers\.co\.za\/p\/1"/.test(badges.withUrl) && /opens a new tab/.test(badges.withUrl) && /noopener/.test(badges.withUrl), "store label links to the store's product page");
check(/^<span class="badge"/.test(badges.without), "store label is plain text when there is no product link");
check((await page.locator('[data-act="newsearch"]').count()) === 0, "a free account is offered Request an item, not the paid new-item search");
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
check((await page.locator("#main h2.h2").first().textContent()).trim() === "My picked Items List", "the list page is headed My picked Items List");
check((await page.locator('[data-act="share"]').first().textContent()).trim() === "Share List on WhatsApp", "the share button says Share List on WhatsApp");
const totals = await page.$$eval("#where ~ .card .tot-row", (rows) => rows.map((r) => { const a = r.querySelector(".amt, .p"), b = r.getBoundingClientRect(), x = a.getBoundingClientRect(); return b.right - x.right <= 12 && x.left > b.left + b.width / 2; }));
check(totals.length > 0 && totals.every(Boolean), "every store total sits at the right-hand edge of its row (" + totals.length + " rows)");
check(!/over 2 days old/.test(await page.textContent("#main")), "no warning bar about old prices on the list page");
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
{
  await page.waitForFunction(() => document.querySelector("#dlg").open && document.querySelector("#dlg").textContent.includes("TrolleyUp plan"));
  const pd = await page.textContent("#dlg");
  check(/R ?39 a month/.test(pd) && !/R ?99/.test(pd), "a free account tapping Send to TrolleyUp is offered the R39 TrolleyUp plan");
  let posted = "";
  await ctx.route("https://sandbox.payfast.co.za/**", (route) => { posted = route.request().postData() || ""; route.fulfill({ status: 200, contentType: "text/html", body: "<h1>PayFast test page</h1>" }); });
  await page.locator('#dlg [data-dlg="upgrade"]').click();
  await page.waitForURL(/sandbox\.payfast\.co\.za/, { timeout: 10000 }).catch(() => {});
  check(/amount=39\.00/.test(posted) && /subscription_type=1/.test(posted) && /signature=[0-9a-f]{32}/.test(posted), "the plan button sends a signed R39 monthly subscription to PayFast (test mode)");
  // the account is now given the plan by hand, as a paid subscription would
  const { execFileSync: run } = require("child_process");
  run(process.env.PP_PYTHON || "python3", ["admin.py", "plan", "dev-tester", "paid"], { cwd: process.env.PP_SMOKE_BACKEND, env: process.env });
  await page.goto(base + "/#list");
  await page.waitForSelector(".li");
  await page.getByRole("button", { name: /^Send to TrolleyUp/ }).waitFor();
  await page.getByRole("button", { name: /^Send to TrolleyUp/ }).click();
}
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

// 5. Clear, then the 29-a-day limit (the fast sessions are made through the app's own API).
for (let n = 1; n <= 3; n++) {
  await page.getByRole("button", { name: /^List/ }).click();
  await page.getByRole("button", { name: "Clear session" }).click();
  await page.locator('#dlg [data-dlg="doclear"]').click();
  await page.waitForFunction(() => !document.querySelector(".li"));
  if (n < 3) { await lookUp(page, "milk"); await page.locator("article.card:has(.best)").first().waitFor(); }
}
await page.evaluate(async () => {
  const pid = (await api("GET", "/catalogue?" + qs({ region: state.region, q: "milk", limit: 1 }))).items[0].id;
  for (let n = 0; n < 26; n++) { await api("POST", "/session/items", { region: state.region, product_id: pid }); await api("POST", "/session/clear", { region: state.region }); }
  await refreshMe();
  render();
});
check(/No lookup sessions left today/.test(await meter(page)), "after 29 sessions: " + await meter(page));
await lookUp(page, "milk");
await page.waitForFunction(() => document.querySelector("#dlg").open);
const dlg = await page.textContent("#dlg");
check(/used your 29 lookup sessions/.test(dlg) && !/R ?39|upgrade/i.test(dlg), "the 30th session is blocked with no upgrade offer (paid users have the same limit)");
await shot(page, "7-limit");
await page.locator('#dlg [data-dlg=""]').click();

// 5b. A script that looks things up faster than a person is paused, on any plan.
{
  const botCtx = await browser.newContext({ viewport: { width: 412, height: 915 } });
  const bot = await botCtx.newPage();
  await signIn(bot, "scriptbot", makeId("580808"));
  const out = await bot.evaluate(async () => {
    const pid = (await api("GET", "/catalogue?" + qs({ region: state.region, q: "milk", limit: 1 }))).items[0].id;
    let ok = 0, code = "";
    for (let n = 0; n < 60 && !code; n++) {
      try { await api("POST", "/session/items", { region: state.region, product_id: pid }); ok++; } catch (e) { code = e.code; }
    }
    return { ok, code };
  });
  check(out.code === "locked" && out.ok >= 30 && out.ok <= 45, "a fast script is paused after about 40 lookups in a minute: " + JSON.stringify(out));
  await bot.reload();
  await bot.waitForSelector(".cats8");
  await bot.fill("#q", "milk");
  await bot.waitForFunction(() => /paused/.test(document.getElementById("main").textContent));
  check(true, "a paused account is told why in plain words");
  await bot.goto(base + "/#account");
  await bot.waitForSelector(".plan");
  check(/Bots, scripts and scraping are not allowed/.test(await bot.textContent("#main")), "My account says bots and scraping are not allowed");
}

// 6. Account screen.
await page.goto(base + "/#account");
await page.waitForSelector(".plan");
const acct = await page.textContent("#main");
check(/TrolleyUp plan/.test(acct) && /Used today: 29 of 29/.test(acct) && /Delete my PricePulse data/.test(acct), "account screen shows the TrolleyUp plan, usage and delete");
await shot(page, "8-account", true);

// 7. Under 18: liquor hidden.
const kidCtx = await browser.newContext({ viewport: { width: 412, height: 915 } });
const kid = await kidCtx.newPage();
await signIn(kid, "kid", MINOR);
check(!(await kid.textContent(".cats8")).includes("Liquor"), "under 18: no Liquor category");
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
check(sizes.body >= 16, "base font is at least 16px (" + sizes.body + ")");
check(sizes.price >= 24, "prices are at least 24px (" + sizes.price + ")");
check(sizes.smallest >= 44, "tap targets at least 44px (" + sizes.smallest.toFixed(1) + ")");
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
  await adm.waitForSelector(".cats8");
  // New-item search: a tester (paid) account sees the search button when nothing matches.
  execFileSync(process.env.PP_PYTHON || "python3", ["admin.py", "plan", "dev-admin", "tester"], { cwd: process.env.PP_SMOKE_BACKEND, env: process.env });
  await adm.reload();
  await adm.waitForSelector(".cats8");
  await adm.fill("#q", "pedigree dog food 8kg");
  await adm.waitForSelector('[data-act="newsearch"]');
  check(/Not in our list yet/.test(await adm.textContent("#main")), "a paid account is offered a search of the stores when nothing matches");
  await shot(adm, "new-item-offer");
  await adm.locator('[data-act="newsearch"]').click();
  await adm.waitForSelector('[data-act="checksearch"]');
  check(/Searching the stores for .pedigree dog food 8kg./.test(await adm.textContent("#main")), "the search is queued and the shopper is told it takes a few minutes");
  await shot(adm, "new-item-waiting");
  await adm.fill("#q", "");
  await adm.waitForSelector(".cats8");
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
  check(sm >= 43.5, "Admin controls are at least 44px tall (" + sm.toFixed(1) + ")");
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
  await adm.selectOption("#lstype", "Butchery");
  await adm.click('#dlg button:has-text("Save")');
  await adm.waitForSelector(".admtable tbody tr b");
  check(/Paarl Corner Cafe/.test(await adm.textContent(".admtable")), "a local store can be added");
  const lsCsv = "item,price,size,sku,department,category,image\r\nBoerewors,89.90,1kg,BW1,Butchery,Fresh,https://img.example.com/w.jpg\r\nTannie Rusks,45,500g,RK1,Bakery,Groceries,http://img.example.com/r.jpg\r\nBad Row,0,,,,,\r\n";
  const [chooser] = await Promise.all([adm.waitForEvent("filechooser"), adm.click('[data-adm="lsimport"]')]);
  await chooser.setFiles({ name: "week1.csv", mimeType: "text/csv", buffer: Buffer.from(lsCsv, "utf8") });
  await adm.waitForSelector("#dlg[open] #lsreplace");
  const lsPrev = await adm.textContent("#dlg");
  check(/3 rows/.test(lsPrev) && /2<\/b> new items|2 new items/.test(lsPrev.replace(/\s+/g, " ")) && /1 rows skipped|1 row/.test(lsPrev.replace(/\s+/g, " ")), "the weekly file is checked first: " + lsPrev.replace(/\s+/g, " ").slice(0, 120));
  check(/up to <b>1000<\/b> items|up to 1000 items/.test(lsPrev.replace(/\s+/g, " ")), "the preview shows the item limit");
  check(/1<\/b> picture links found|1 picture links found/.test(lsPrev.replace(/\s+/g, " ")) && /not usable/.test(lsPrev), "the preview counts picture links and bad ones");
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
  // New items in the last 24 hours (admin only).
  await adm.click('[data-adm="tab"][data-tab="overview"]');
  await adm.waitForSelector('[data-adm="new24"]');
  const newBtn = await adm.textContent('[data-adm="new24"]');
  check(/New items in the last 24 hours: \d+/.test(newBtn), "Admin Overview has a button with the number of new items in the last 24 hours: " + newBtn.trim());
  await adm.click('[data-adm="new24"]');
  await adm.waitForSelector("#admadded");
  check((await adm.inputValue("#admadded")) === "24h", "the button opens the Items list filtered to the last 24 hours");
  await adm.waitForSelector(".admtable th:has-text('Added')");
  check(true, "the list has an Added column when filtered to the last 24 hours");
  await adm.selectOption("#admadded", "");
  await adm.waitForFunction(() => !/Added<\/th>/.test(document.querySelector("#main").innerHTML));
  check(true, "choosing Any time brings the full list back");
  // 11. Store-owner portal: an owner runs one store (items by hand and by CSV); shoppers find the store.
  const py = process.env.PP_PYTHON || "python3", cwd = process.env.PP_SMOKE_BACKEND;
  const created = await adm.evaluate(() => api("POST", "/admin/localstores", { name: "Paarl Refurb PCs", town_slug: "paarl", address: "12 Main Road, Paarl", contact_name: "", phone: "082 123 4567", email: "", notes: "", store_type: "IT & Electronics", max_items: 200, active: true, category: "Electronics", whatsapp: "", paid_until: "" }));
  const ownCtx = await browser.newContext({ viewport: { width: 1360, height: 900 } });
  const own = await ownCtx.newPage();
  const ownErrors = [];
  own.on("pageerror", (e) => ownErrors.push(e.message));
  await own.goto(base + "/");
  await own.waitForSelector("#devform");
  await own.fill("#devform [name=name]", "shopowner");
  await own.click("#devform button");
  await own.waitForSelector("#idform");
  check(true, "an account that is not linked to a store gets the ordinary age check");
  execFileSync(py, ["admin.py", "store-owner", String(created.id), "dev-shopowner"], { cwd, env: process.env });
  await own.reload();
  await own.waitForSelector(".admgrid");
  check(/My store/.test(await own.textContent("#main")) && /0 of 200/.test(await own.textContent("#main")), "a linked store owner lands on My store with no age check: 0 of 200 items");
  check(await own.evaluate(() => document.body.classList.contains("adm-wide")), "My store uses the wide desktop layout");
  await own.click("#menubtn");
  check(await own.isVisible('#menupop [data-menu="mystore"]'), "My store is in the three-dot menu for a store owner");
  await own.click("#menubtn");
  // Add an item by hand: a bad price is explained, a good one saves.
  await own.click('[data-so="tab"][data-tab="items"]');
  await own.locator('[data-so="add"]').first().click();
  await own.fill("#soname", "Lenovo ThinkPad T480 14in");
  await own.selectOption("#sosub", "Laptops");
  await own.selectOption("#socond", "Refurbished");
  await own.fill("#soprice", "abc");
  await own.click('[data-dlg^="sosave"]');
  await own.waitForFunction(() => /price/.test((document.getElementById("soerr") || {}).textContent || ""));
  check(true, "a bad price is explained in plain words");
  await own.fill("#soprice", "5499");
  await own.fill("#sosize", "i5 16GB 512GB SSD");
  await own.click('[data-dlg^="sosave"]');
  await own.waitForSelector('.admtable td:has-text("ThinkPad")');
  check(/own-1/.test(await own.textContent(".admtable")) && /Refurbished/.test(await own.textContent(".admtable")), "the item is listed with an item code and its condition");
  // Edit it, then upload a CSV file.
  await own.locator('[data-so="edit"]').first().click();
  await own.fill("#soprice", "5299");
  await own.click('[data-dlg^="sosave"]');
  await own.waitForSelector('.admtable td:has-text("5 299")');
  check(true, "an item can be edited");
  await own.click('[data-so="tab"][data-tab="upload"]');
  const csv = "item,price,sub-category,condition,sku\nDell OptiPlex 7050 SFF,3999,Desktops,Refurbished,DELL7050\nLogitech K120 Keyboard,189,Peripherals,New,LOGK120\niPhone 11 64GB,5999,iPhones,Refurbished,IP11\nBad Row,abc,Laptops,New,BAD\n";
  await own.setInputFiles("#sofile", { name: "stock.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await own.waitForSelector('#dlg h2:has-text("Load this file")');
  const pre = await own.textContent("#dlg");
  check(/3<\/b> new items|3 new items/.test(pre.replace(/\s+/g, " ")) || /\b3\b.*new items/.test(pre) , "the CSV preview shows 3 new items and skips the bad row");
  check(/skipped/.test(pre) && /4<\/b> items|4 items|\b4\b/.test(pre), "the preview says how many items the store would have");
  await own.click('[data-dlg="soload"]');
  await own.waitForSelector('#dlg h2:has-text("File loaded")');
  await own.click('#dlg [data-dlg=""]');
  await own.waitForSelector('.admtable td:has-text("OptiPlex")');
  check(/ThinkPad/.test(await own.textContent(".admtable")), "the hand-typed item is still there after the CSV (add and update only)");
  // Store details shoppers see.
  await own.click('[data-so="tab"][data-tab="details"]');
  await own.fill("#sodwa", "+27 82 111 2222");
  await own.click('[data-so="savedetails"]');
  await own.waitForFunction(() => /Saved/.test(document.getElementById("status").textContent));
  check(true, "store details can be saved");
  await shot(own, "store-owner-items");
  // Opening hours and shop photos from the owner's side.
  const hrGeo = await own.evaluate(() => { const r = document.querySelector(".hrsedit .hr2").getBoundingClientRect(); const t = document.querySelector("#soho-mon"); return { w: Math.round(r.width), ok: document.querySelector("#soho-mon").type === "time" }; });
  check(hrGeo.ok && hrGeo.w <= 480, "opening hours are a compact Google-Maps style list with time pickers (row " + hrGeo.w + "px wide)");
  await own.check("#sohx-mon");
  await own.fill("#soho-mon", "00:00");
  await own.fill("#sohc-mon", "23:59");
  await own.click('[data-so="copymon"]');
  for (const d of ["sat", "sun"]) { await own.check("#sohx-" + d); await own.fill("#soho-" + d, "00:00"); await own.fill("#sohc-" + d, "23:59"); }
  await own.fill("#sohnote", "Closed on public holidays");
  await own.click('[data-so="savehours"]');
  await own.waitForFunction(() => /Opening hours saved/.test(document.getElementById("status").textContent));
  await own.waitForFunction(() => /Open now|Closing soon/.test((document.getElementById("sohprev") || {}).textContent || ""));
  check(/Open now|Closing soon/.test(await own.textContent("#sohprev")), "the owner types opening hours and sees what shoppers will read");
  await own.fill("#soho-tue", "17:00"); await own.fill("#sohc-tue", "08:00");
  await own.click('[data-so="savehours"]');
  await own.waitForFunction(() => /later than/.test((document.getElementById("soherr") || {}).textContent || ""));
  check(true, "bad opening hours are explained in plain words");
  const png = await own.evaluate(() => { const c = document.createElement("canvas"); c.width = 1600; c.height = 1200; const g = c.getContext("2d"); g.fillStyle = "#3b6ea5"; g.fillRect(0, 0, 1600, 1200); g.fillStyle = "#fff"; g.fillRect(200, 300, 1200, 600); return c.toDataURL("image/png").split(",")[1]; });
  await own.click('[data-so="tab"][data-tab="overview"]');
  await own.waitForSelector(".photoslot");
  check(/Shop photos/.test(await own.textContent("#main")) && !(await own.locator('[data-so="add"]').count()), "Shop photos are on the Overview page, and Add an item is not");
  const [photoChooser] = await Promise.all([own.waitForEvent("filechooser"), own.click('[data-so="photo"][data-slot="1"]')]);
  await photoChooser.setFiles({ name: "front.png", mimeType: "image/png", buffer: Buffer.from(png, "base64") });
  await own.waitForSelector('.photoslot .thumb img');
  check(await own.locator('.photoslot .thumb img').count() === 1, "the owner uploads a photo from his computer (shrunk in the browser) and sees it");
  await shot(own, "store-owner-details");
  // Overview facts, hours summary, how the store sells.
  await own.click('[data-so="tab"][data-tab="overview"]');
  await own.waitForSelector(".admgrid .stat");
  const ov = await own.textContent("#main");
  check(/Paarl Refurb PCs · Paarl/.test(ov) && /\d{13}/.test(ov) && /Items on your plan used/.test(ov) && /Paid until/.test(ov) && /Last change to store/.test(ov) && /Store status/.test(ov), "Overview shows the store name as shoppers see it, the Store ID, status, items used, paid until and last change");
  check(/Business hours/.test(ov) && /Mon–Sun\s*00:00–23:59/.test(ov), "Overview summarises the business hours (Mon–Sun 00:00–23:59)");
  check(/Physical shop/.test(ov), "Overview says how the store sells (Physical shop)");
  check((await own.locator('[data-so="add"]').count()) === 0 && (await own.locator('[data-so="gotab"][data-tab="upload"]').count()) === 0, "Overview no longer has the Add an item and Upload a CSV buttons");
  await shot(own, "mystore-overview", true);
  // Items page: buttons on top, small text, no sideways scroll, tick and remove.
  await own.click('[data-so="tab"][data-tab="items"]');
  await own.waitForSelector(".admtable.compact td");
  check((await own.locator(".admbar").first().textContent()).includes("Add an item") && (await own.locator(".admbar").first().textContent()).includes("Upload a CSV file"), "Add an item and Upload a CSV file are at the top of the Items page");
  const itemsGeo = await own.evaluate(() => { const w = document.querySelector(".admwrap"); const td = document.querySelector(".admtable.compact td.nm"); return { fits: w.scrollWidth <= w.clientWidth + 1, font: parseFloat(getComputedStyle(td).fontSize) }; });
  check(itemsGeo.fits && itemsGeo.font <= 15, "the Items table fits without sideways scrolling and uses small text (" + itemsGeo.font + "px)");
  await shot(own, "mystore-items", true);
  await own.locator('[data-so="add"]').first().click();
  await own.fill("#soname", "Temp USB Cable");
  await own.selectOption("#sosub", "Peripherals");
  await own.selectOption("#socond", "New");
  await own.fill("#soprice", "49");
  await own.click('[data-dlg^="sosave"]');
  await own.waitForSelector('.admtable td:has-text("Temp USB Cable")');
  await own.locator('tr:has-text("Temp USB Cable") input[data-so="tick"]').check();
  await own.click('[data-so="removesel"]');
  await own.click('#dlg [data-dlg="soremovesel"]');
  await own.waitForFunction(() => !/Temp USB Cable[\s\S]*Remove<\/button>/.test(document.querySelector("#main").innerHTML) || /Hidden/.test(document.querySelector("tr").innerText));
  await own.waitForSelector('tr:has-text("Temp USB Cable"):has-text("Hidden")');
  check(true, "ticked items can be removed from the shopper screens");
  // A shopper: Electronics icon screen, rows, View page, store page, Enter Store.
  const buyCtx = await browser.newContext({ viewport: { width: 412, height: 915 } });
  const buyer = await buyCtx.newPage();
  const buyErrors = [];
  buyer.on("pageerror", (e) => buyErrors.push(e.message));
  await signIn(buyer, "buyer", makeId("751212"));
  await buyer.locator('.cats8 .cat[data-cat="Electronics"]').click();
  await buyer.waitForSelector('.cats8.sub3 .cnt2');
  const tiles = buyer.locator('.cats8.sub3 [data-fsub]');
  check((await tiles.count()) === 6, "Electronics opens an icon screen with six sub-categories");
  const labs = await tiles.evaluateAll((els) => els.map((e) => e.querySelector(".lbl").textContent));
  check(JSON.stringify(labs) === JSON.stringify(["Laptops", "Desktops", "Screens", "Peripherals", "Mobile phones", "iPhones"]), "the six icons are in the owner's order: " + labs.join(", "));
  const geo = await buyer.evaluate(() => { const r = [...document.querySelectorAll(".cats8.sub3 .cat")].map((e) => e.getBoundingClientRect()); const c = document.querySelector(".cats8.sub3 .circ").getBoundingClientRect(); const f = document.querySelector('.cats8.sub3 .lbl'); return { rows: new Set(r.map((x) => Math.round(x.top))).size, perRow: r.filter((x) => Math.round(x.top) === Math.round(r[0].top)).length, circ: c.width, font: parseFloat(getComputedStyle(f).fontSize), scroll: document.documentElement.scrollWidth <= innerWidth }; });
  check(geo.rows === 2 && geo.perRow === 3 && geo.circ >= 48 && geo.font >= 16 && geo.scroll, "the icon screen is 3 x 2 with the front-page circle and label size, no sideways scroll: " + JSON.stringify(geo));
  console.log('INFO tile text:', JSON.stringify(await tiles.evaluateAll((e)=>e.map((x)=>x.textContent))));
  check(/Laptops\s*1/.test((await tiles.nth(0).textContent()).trim()) || /1/.test(await tiles.nth(0).textContent()), "each icon shows how many items it holds");
  await shot(buyer, "electronics-icons");
  await buyer.locator('[data-fsub="Desktops"]').click();
  await buyer.waitForSelector('article.lrow:has-text("OptiPlex")');
  check((await buyer.locator("article.lrow").count()) === 1, "a sub-category lists its items as rows");
  const row = buyer.locator("article.lrow").first();
  const rt = await row.textContent();
  check(/Refurbished/.test(rt) && /R\s*3\s?999/.test(rt) && /Paarl Refurb PCs/.test(rt) && (await row.locator(".lpic").count()) === 1 && (await row.locator("a.viewbtn").count()) === 1, "a row has picture box, name, price shown without a lookup, Refurbished tag, store and a View button");
  const rg = await buyer.evaluate(() => { const r = document.querySelector("article.lrow"); const v = r.querySelector(".viewbtn").getBoundingClientRect(); const pr = parseFloat(getComputedStyle(r.querySelector(".price")).fontSize); return { h: r.getBoundingClientRect().height, vh: v.height, price: pr, scroll: document.documentElement.scrollWidth <= innerWidth }; });
  check(rg.vh >= 44 && rg.price >= 24 && rg.scroll, "row: View button 44px or more, price 24px or more, no sideways scroll: " + JSON.stringify(rg));
  check(/Lookup|lookup/.test(await buyer.textContent("#main")) === false, "no Look up button on a local row");
  await shot(buyer, "electronics-rows");
  await buyer.locator('[data-fcond="New"]').click();
  await buyer.waitForFunction(() => /No items of this kind/.test(document.getElementById("main").textContent));
  check(true, "the New / Refurbished switch filters the rows");
  await buyer.locator('[data-fcond=""]').click();
  await buyer.waitForSelector("article.lrow");
  await buyer.locator("article.lrow a.viewbtn").first().click();
  await buyer.waitForSelector('a:has-text("Enquire about this item")');
  const vp = await buyer.textContent("#main");
  check(/OptiPlex/.test(vp) && /3 999/.test(vp.replace(/\s+/g, " ").replace("R3 999", "3 999")) || /3\s?999/.test(vp), "View page shows the item and its price");
  check(/Open now|Closing soon/.test(vp) && /12 Main Road/.test(vp) && (await buyer.locator('a:has-text("Enter Store")').count()) === 1, "View page has the store card: status, address, Enter Store");
  const enq = await buyer.getAttribute('a:has-text("Enquire about this item")', "href");
  check(/^https:\/\/wa\.me\/27821112222\?text=/.test(enq) && /OptiPlex/.test(decodeURIComponent(enq)), "Enquire about this item opens WhatsApp to the store with the item typed in");
  check((await buyer.locator('section.place a[href^="https://wa.me/"]:not([href*="?text="])').count()) === 0 && (await buyer.locator('section.place a:has-text("Enquire about this item")').count()) === 1 && (await buyer.locator('section.place a[href^="tel:"]').count()) >= 1, "the item page keeps Call and Enquire about this item and has no separate WhatsApp button");
  await shot(buyer, "item-view");
  await buyer.locator('section.place h3 a').click();
  await buyer.waitForSelector("section.place .photos");
  const sp = await buyer.textContent("#main");
  check((await buyer.locator("section.place .photos img").count()) === 1, "the store page shows the shop photo");
  check(/Paarl Refurb PCs/.test(sp) && /Open now|Closing soon/.test(sp) && /12 Main Road/.test(sp), "the store page has title, opening-hours status and address");
  check((await buyer.locator('a[href^="tel:"]').count()) >= 1 && (await buyer.locator('a[href^="https://wa.me/27821112222"]').count()) >= 1 && (await buyer.locator('a:has-text("Directions")').count()) === 1, "the store page has Call, WhatsApp and Directions");
  await buyer.locator("section.place summary").click();
  check(/Monday|Mon/.test(await buyer.textContent("section.place details")) && /public holidays/.test(await buyer.textContent("section.place details")), "See all hours lists the week and the owner's note");
  check(await buyer.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "the store page has no sideways scroll");
  await shot(buyer, "store-page");
  await buyer.locator('a:has-text("Enter Store")').click();
  await buyer.waitForSelector('.cats8.sub3 [data-shop="sub"]');
  check((await buyer.locator('.cats8.sub3 [data-shop="sub"]').count()) >= 3, "Enter Store shows the store's own sub-category icons");
  await buyer.locator('[data-shop="sub"][data-sub="Laptops"]').click();
  await buyer.waitForSelector('article.lrow:has-text("ThinkPad")');
  check((await buyer.locator("article.lrow").count()) === 1, "a store sub-category lists only that store's items of that kind");
  await buyer.setViewportSize({ width: 360, height: 780 });
  check(await buyer.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "the rows have no sideways scroll at 360px wide");
  await shot(buyer, "store-items-360");
  // 12. A person signs up a store online; it waits for approval; shoppers within 100 km see it after approval.
  const nsCtx = await browser.newContext({ viewport: { width: 412, height: 915 } });
  const ns = await nsCtx.newPage();
  const nsErrors = [];
  ns.on("pageerror", (e) => nsErrors.push(e.message));
  await ns.goto(base + "/");
  await ns.waitForSelector("#devform");
  check(await ns.locator('[data-act="sellsignin"]').isVisible(), "the sign-in screen has an 'I run a store: list my items' button");
  await ns.click('[data-act="sellsignin"]');
  await ns.fill("#devform [name=name]", "newshop");
  await ns.click("#devform button.btn");
  await ns.waitForSelector("#sellform");
  check(!/ID number/.test(await ns.textContent("#main")), "a new store owner goes to the store sign-up form, not the age check");
  check(await ns.locator('#sellform [name=category] option').count() === 8, "the form offers the seven categories to choose one from");
  check(await ns.isHidden("#sellliq"), "the liquor licence box is hidden until Liquor is chosen");
  await ns.selectOption("#sellform [name=category]", "Liquor");
  check(await ns.isVisible("#sellliq"), "choosing Liquor shows the licence box");
  await ns.selectOption("#sellform [name=category]", "Pets");
  await ns.fill("#sellform [name=name]", "Paw Palace");
  await ns.selectOption("#sellform [name=town]", "franschhoek");
  await ns.selectOption("#sellform [name=mode]", "online");
  await ns.fill("#sellform [name=phone]", "082 555 0101");
  await ns.click("#sellform button[type=submit]");
  await ns.waitForFunction(() => /Please tick/.test((document.getElementById("sellerr") || {}).textContent || ""));
  check(true, "the Store terms must be ticked");
  await ns.check("#sellform [name=terms]");
  await ns.click("#sellform button[type=submit]");
  await ns.waitForSelector("#dlg .big13");
  const sid = (await ns.textContent("#dlg .big13")).trim();
  check(/^\d{13}$/.test(sid), "signing up gives a 13-digit Store ID: " + sid);
  await ns.click('#dlg [data-dlg="sellgo"]');
  await ns.waitForSelector(".admgrid .stat");
  check((await ns.textContent(".admgrid")).includes(sid) && /Waiting for approval/.test(await ns.textContent("#main")), "My store shows the Store ID and 'Waiting for approval'");
  await ns.click('[data-so="tab"][data-tab="items"]');
  await ns.locator('[data-so="add"]').first().click();
  await ns.fill("#soname", "Pedigree Adult Dog Food 8kg");
  await ns.fill("#soprice", "249.99");
  await ns.click('[data-dlg^="sosave"]');
  await ns.waitForSelector('.admtable td:has-text("Pedigree")');
  check(true, "a store waiting for approval can already add items");
  await shot(ns, "store-signup-pending");
  const sh = await buyCtx.newPage();
  await sh.goto(base + "/");
  await sh.waitForSelector(".cats8");
  await sh.fill("#q", "pedigree");
  await sh.waitForTimeout(1500);
  check(!/Pedigree/.test(await sh.textContent("#main")), "shoppers cannot see a store that is waiting for approval");
  await adm.click('[data-adm="tab"][data-tab="items"]');
  await adm.click('[data-adm="tab"][data-tab="localstores"]');
  await adm.waitForSelector('[data-adm="lsapprove"]');
  check(/Paw Palace/.test(await adm.textContent("#main")) && (await adm.textContent("#main")).includes(sid), "Admin sees the application with its Store ID");
  await shot(adm, "admin-pending-store");
  await adm.click('[data-adm="lsapprove"]');
  await adm.fill("#lsappdate", "2099-01-31");
  await adm.click('[data-dlg^="lsapprove"]');
  await adm.waitForFunction(() => !document.querySelector('[data-adm="lsapprove"]'));
  check(true, "Admin approves the store with a paid-until date");
  await sh.fill("#q", "pedigree ");
  await sh.waitForSelector('article:has-text("Pedigree")');
  check(true, "after approval a shopper within 100 km finds the store's item");
  const allStores = await adm.evaluate(() => api("GET", "/admin/localstores"));
  const paw = allStores.stores.find((x) => x.name === "Paw Palace");
  await sh.goto(base + "/#shop/" + paw.id);
  await sh.waitForSelector("section.place");
  const pawText = await sh.textContent("section.place");
  check(/Online only/.test(pawText) && /Online store, serving Franschhoek/.test(pawText) && !/Directions/.test(pawText) && !/Open now|Closed/.test(pawText), "an online-only store still shows its town and has no address, Directions or opening-hours status");
  // 13. Nine provinces: the place chooser groups towns under provinces; location picks the nearest town.
  const pvCtx = await browser.newContext({ viewport: { width: 412, height: 915 }, geolocation: { latitude: -26.15, longitude: 28.05 }, permissions: ["geolocation"] });
  const pv = await pvCtx.newPage();
  const pvErrors = [];
  pv.on("pageerror", (e) => pvErrors.push(e.message));
  await signIn(pv, "traveller", makeId("620610"));
  check((await pv.locator("#base optgroup").count()) === 9, "the place chooser has nine provinces");
  const labs9 = await pv.locator("#base optgroup").evaluateAll((els) => els.map((e) => e.label));
  check(["Eastern Cape", "Free State", "Gauteng", "KwaZulu-Natal", "Limpopo", "Mpumalanga", "North West", "Northern Cape", "Western Cape"].every((n) => labs9.includes(n)), "all nine province names are listed: " + labs9.join(", "));
  check((await pv.inputValue("#base")) === "western-cape|", "a new shopper starts on the Western Cape (whole province)");
  await pv.click("#locate");
  await pv.waitForFunction(() => document.getElementById("base").value.startsWith("gauteng|"));
  check((await pv.inputValue("#base")).startsWith("gauteng|"), "'use my location' near Johannesburg picks a Gauteng town: " + (await pv.inputValue("#base")));
  await pv.fill("#q", "milk");
  await pv.waitForSelector(".grid article");
  check(true, "Gauteng has chain prices");
  await pv.click('[data-view="towns"]');
  check(/Compare towns near me/.test(await pv.textContent("#main")) || /list is empty/.test(await pv.textContent("#main")), "the Towns tab is now 'Compare towns near me'");
  check(await pv.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "no sideways scroll with the longer place list");
  check(pvErrors.length === 0, "no JavaScript errors in the province chooser" + (pvErrors.length ? ": " + pvErrors.join("; ") : ""));
  check(nsErrors.length === 0, "no JavaScript errors on the store sign-up screens" + (nsErrors.length ? ": " + nsErrors.join("; ") : ""));
  check(buyErrors.length === 0, "no JavaScript errors on the shopper screens" + (buyErrors.length ? ": " + buyErrors.join("; ") : ""));
  // A lapsed store disappears; the owner is told.
  execFileSync(py, ["admin.py", "store-paid", String(created.id), "2020-01-01"], { cwd, env: process.env });
  await own.reload();
  await own.waitForSelector(".banner.warn");
  check(/hidden from shoppers/.test(await own.textContent("#main")), "a lapsed store owner is told the store is hidden");
  await buyer.goto(base + "/");
  await buyer.waitForSelector(".cats8");
  await buyer.locator('.cats8 .cat[data-cat="Electronics"]').click();
  await buyer.waitForSelector(".cats8.sub3 .cnt2");
  const zero = await buyer.locator(".cats8.sub3 .cnt2").allTextContents();
  check(zero.every((x) => x.trim() === "0") && !/ThinkPad/.test(await buyer.textContent("#main")), "a lapsed store's items are gone from the shopper screens (every icon shows 0)");
  await buyer.goto(base + "/#shop/" + created.id);
  await buyer.waitForSelector("#main .empty");
  check(true, "a lapsed store's page is not available");
  execFileSync(py, ["admin.py", "store-paid", String(created.id), "none"], { cwd, env: process.env });
  // The admin deletes the test store.
  await adm.goto(base + "/#admin");
  await adm.waitForSelector('[data-adm="tab"][data-tab="localstores"]');
  await adm.click('[data-adm="tab"][data-tab="localstores"]');
  await adm.waitForSelector('[data-adm="lsdelete"]');
  await adm.locator('tr:has-text("Paarl Refurb PCs") [data-adm="lsdelete"]').click();
  await adm.fill("#lsdelname", "Paarl Refurb PCs");
  await adm.click('#dlg [data-dlg^="lsdel:"]');
  await adm.waitForFunction(() => !/Paarl Refurb PCs/.test(document.querySelector("#main").textContent));
  check(true, "the admin can delete a test store (typing its name to confirm)");
  check(ownErrors.length === 0, "no JavaScript errors in the store-owner portal" + (ownErrors.length ? ": " + ownErrors.join("; ") : ""));
  check(admErrors.length === 0, "no JavaScript errors in the admin area" + (admErrors.length ? ": " + admErrors.join("; ") : ""));
}

check(errors.length === 0, "no JavaScript errors" + (errors.length ? ": " + errors.join("; ") : ""));
await browser.close();
console.log(failures ? `\n${failures} check(s) failed.` : "\nAll smoke checks passed.");
process.exit(failures ? 1 : 0);
