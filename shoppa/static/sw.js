// shoppa service worker.
//
// Runs on a LAN/VPN where the network is essentially always reachable and the app
// is still under active development, so freshness beats aggressive caching:
// everything is network-first with a cache fallback. When online you always get
// the latest code/markup; the cached shell only kicks in offline — which for a
// shopping list matters, since supermarket reception is exactly where you'll want
// to read the thing. Live data (/api/, /healthz) is never cached.
//
// The app version arrives as ?v= on the registration URL (see base.html), and is
// the single source of truth for cache busting: it names the cache *and* stamps
// every asset URL, so shipping a release retires the old cache and sidesteps the
// browser's HTTP cache in one move. Nothing to remember to bump by hand.
const V = new URL(self.location.href).searchParams.get("v") || "dev";
const CACHE = `shoppa-${V}`;
const SHELL = [
  "/",
  "/manifest.webmanifest",
  `/static/style.css?v=${V}`,
  `/static/phone.js?v=${V}`,
  `/static/htmx.min.js?v=${V}`,
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (e) => {
  // Deliberately not addAll(): that goes through the browser's HTTP cache, so a
  // stale script could be pinned into a freshly-versioned cache and survive the
  // very deploy meant to replace it. `reload` forces a real trip to the network.
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => Promise.all(SHELL.map((u) =>
        fetch(u, { cache: "reload" }).then((r) => (r.ok ? c.put(u, r) : null))
      )))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return; // never intercept mutations
  const url = new URL(e.request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname === "/healthz") return;

  // Same reasoning as install: bypass the HTTP cache for assets so the worker
  // can't re-cache something stale. Navigations are left alone — a Request in
  // navigate mode can't be safely rebuilt with an init, and the HTML is already
  // coming back fresh.
  const opts = url.pathname.startsWith("/static/") ? { cache: "no-store" } : undefined;

  e.respondWith(
    fetch(e.request, opts)
      .then((resp) => {
        if (resp && resp.ok) {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return resp;
      })
      .catch(() =>
        caches.match(e.request).then(
          (cached) => cached || (e.request.mode === "navigate" ? caches.match("/") : undefined)
        )
      )
  );
});
