// Frontend smoke test (Playwright). Run through run_smoke.py, which starts the API with example data.
//   node tests/smoke.mjs http://127.0.0.1:8765  [screenshot-folder]
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const base = process.argv[2] || "http://127.0.0.1:8000";
const shots = process.argv[3] || "";
const launch = process.env.PP_CHROMIUM_PATH ? { executablePath: process.env.PP_CHROMIUM_PATH } : {};
let failures = 0;
function check(ok, what) { console.log((ok ? "PASS " : "FAIL ") + what); if (!ok) failures++; }

const browser = await chromium.launch(launch);
const ctx = await browser.newContext({ viewport: { width: 412, height: 915 }, deviceScaleFactor: 1 });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));

await page.goto(base + "/");
await page.waitForFunction(() => document.getElementById("fresh").textContent.startsWith("Prices updated"), null, { timeout: 15000 });
check(true, "app loads and shows when prices were updated: " + await page.textContent("#fresh"));

await page.fill("#q", "milk");
const cards = page.locator("article.card");
check(await cards.count() >= 1, "search 'milk' shows products (" + await cards.count() + ")");
const first = cards.first();
const stores = await first.locator(".badge").count();
check(stores >= 3, "first milk product shows at least 3 stores (" + stores + ")");
check((await first.textContent()).includes("Best price"), "best price is labelled in text, not only colour");

await first.getByRole("button", { name: /^Add .* to list$/ }).click();
check(await first.getByRole("button", { name: /^Remove .* from list$/ }).count() === 1, "item added to list");
if (shots) await page.screenshot({ path: shots + "/compare-light.png", fullPage: false });

await page.getByRole("button", { name: /^List/ }).click();
const single = page.locator("#single");
await single.waitFor();
const singleText = await single.textContent();
check(/Cheapest/.test(singleText) && /R \d/.test(singleText), "cheapest single store shown: " + singleText.replace(/\s+/g, " ").trim().slice(0, 60));
check(await page.locator("#split").count() === 1, "split shop shown");
if (shots) await page.screenshot({ path: shots + "/list-light.png", fullPage: true });

// Product detail with price history.
await page.getByRole("button", { name: /^Compare/ }).click();
await page.fill("#q", "milk");
await page.locator("article.card h3 a").first().click();
await page.waitForSelector("svg.chart, #main .sub", { timeout: 10000 });
await page.waitForSelector("svg.chart", { timeout: 10000 }).catch(() => {});
check(await page.locator("svg.chart").count() === 1, "price history chart drawn");
if (shots) await page.screenshot({ path: shots + "/detail-light.png", fullPage: true });

// Towns tab uses the server.
await page.getByRole("button", { name: /^Towns/ }).click();
await page.waitForSelector(".town, .empty, .banner", { timeout: 10000 });
await page.waitForTimeout(500);
check(await page.locator(".town").count() >= 1, "towns tab shows real town totals");
if (shots) await page.screenshot({ path: shots + "/towns-light.png", fullPage: true });

// Offline: reload with the network cut. The app shell comes from the service worker,
// prices from the saved copy, with a visible banner.
await page.goto(base + "/#compare");
await page.waitForFunction(() => navigator.serviceWorker && navigator.serviceWorker.controller, null, { timeout: 10000 }).catch(() => {});
await ctx.setOffline(true);
await ctx.route("**/api/**", (route) => route.abort("internetdisconnected"));
await page.reload();
await page.waitForSelector(".banner", { timeout: 15000 });
const banner = (await page.locator(".banner").allTextContents()).join(" | ");
const m = banner.match(/Showing saved prices from[^.]*\./);
check(!!m, "offline shows saved prices banner: " + (m ? m[0] : banner.slice(0, 80)));
await ctx.unroute("**/api/**");
await ctx.setOffline(false);
if (shots) await page.screenshot({ path: shots + "/offline-light.png" });

// Dark theme and large text.
const dark = await browser.newContext({ viewport: { width: 412, height: 915 }, colorScheme: "dark", reducedMotion: "reduce" });
const dp = await dark.newPage();
await dp.goto(base + "/#compare");
await dp.waitForFunction(() => document.getElementById("fresh").textContent.startsWith("Prices updated"), null, { timeout: 15000 });
const bg = await dp.evaluate(() => getComputedStyle(document.body).backgroundColor);
check(bg === "rgb(21, 15, 16)", "dark theme follows the phone setting");
const sizes = await dp.evaluate(() => ({
  body: parseFloat(getComputedStyle(document.body).fontSize),
  price: parseFloat(getComputedStyle(document.querySelector(".price")).fontSize),
  smallestButton: Math.min(...[...document.querySelectorAll("button, select, input, label.btn")]
    .filter((b) => b.offsetParent && !b.closest(".vh"))
    .map((b) => Math.min(b.getBoundingClientRect().height, b.getBoundingClientRect().width))),
}));
check(sizes.body >= 17, "base font is at least 17px (" + sizes.body + ")");
check(sizes.price >= 28, "prices are at least 28px (" + sizes.price + ")");
check(sizes.smallestButton >= 48, "tap targets at least 48px (" + sizes.smallestButton.toFixed(1) + ")");
if (shots) {
  await dp.screenshot({ path: shots + "/compare-dark.png" });
  await dp.goto(base + "/#towns"); await dp.waitForTimeout(800);
  await dp.screenshot({ path: shots + "/towns-dark.png", fullPage: true });
}

check(errors.length === 0, "no JavaScript errors" + (errors.length ? ": " + errors.join("; ") : ""));
await browser.close();
console.log(failures ? `\n${failures} check(s) failed.` : "\nAll smoke checks passed.");
process.exit(failures ? 1 : 0);
