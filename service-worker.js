// Service worker minimal : met en cache la coquille de l'app pour qu'elle
// s'ouvre instantanément, sans bloquer les mises à jour de score.json qui
// doit toujours être rechargé depuis le réseau (données du jour).
const CACHE_NAME = "analyse-or-shell-v1";
const SHELL_FILES = ["./index.html", "./manifest.json"];

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
  if (url.pathname.endsWith("score.json")) {
    event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
    return;
  }

  // reste de la coquille : cache d'abord, réseau en secours
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
