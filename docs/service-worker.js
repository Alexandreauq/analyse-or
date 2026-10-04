// Service worker minimal : met en cache la coquille de l'app pour qu'elle
// s'ouvre instantanément, sans bloquer les mises à jour de score.json qui
// doit toujours être rechargé depuis le réseau (données du jour).
const CACHE_NAME = "analyse-or-shell-v88";
// index.html charge chart_patterns.js/scalping.js/portfolio.js à la demande
// (premier clic sur chaque onglet) — les précacher ici garantit qu'ils sont
// disponibles hors-ligne dès la première ouverture de l'app, pas seulement
// après une première visite en ligne de chaque onglet. company_quote_widget.js
// est chargé en amont (dès index.html, pas à la demande) mais précaché ici
// pour la même raison : disponible hors-ligne dès la première ouverture.
const SHELL_FILES = [
  "./index.html",
  "./manifest.json",
  "./chart_patterns.js",
  "./scalping.js",
  "./portfolio.js",
  "./company_quote_widget.js",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_FILES))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // score.json : toujours réseau d'abord (données fraîches), jamais de cache
  if (url.pathname.endsWith("score.json") || url.pathname.endsWith("indices.json") || url.pathname.endsWith("real_portfolio.json") || url.pathname.endsWith("real_portfolio_mt5.json")) {
    event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
    return;
  }

  // reste de la coquille (index.html, scripts, icônes) : réseau d'abord, pour
  // qu'une mise en ligne soit visible sans rechargement forcé. Le cache ne sert
  // qu'en secours hors ligne. Chaque réponse réussie alimente le cache.
  event.respondWith(
    fetch(event.request).then((response) => {
      if (response.ok && url.origin === self.location.origin) {
        const toCache = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, toCache));
      }
      return response;
    }).catch(() => caches.match(event.request))
  );
});
