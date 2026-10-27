/* Service worker: keeps the app shell available offline.
   Prices are NOT cached here; the app saves the last prices itself and shows
   "Showing saved prices from ..." when offline. Bump VERSION on each release. */
var VERSION = "pp-shell-v1";
var SHELL = ["./", "index.html", "config.js", "manifest.webmanifest", "icons/icon.svg",
             "icons/icon-192.png", "icons/icon-512.png", "icons/icon-180.png"];

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(VERSION).then(function (c) { return c.addAll(SHELL); }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== VERSION; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener("fetch", function (e) {
  var req = e.request;
  var url = new URL(req.url);
  if (req.method !== "GET" || url.pathname.indexOf("/api/") !== -1 || url.origin !== location.origin) return;
  // Network first for the app shell (so updates arrive), cache when offline.
  e.respondWith(fetch(req).then(function (res) {
    if (res.ok) { var copy = res.clone(); caches.open(VERSION).then(function (c) { c.put(req, copy); }); }
    return res;
  }).catch(function () {
    return caches.match(req).then(function (hit) { return hit || caches.match("index.html"); });
  }));
});
