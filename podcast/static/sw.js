// Nejmenší možný service worker: nic necachuje, jen prohlížeči dokazuje, že
// aplikace umí běžet samostatně, a tím odemyká nabídku „Přidat na plochu“.
//
// Cachovat tu schválně nechceme — administrace ukazuje stav běhů a fronty,
// a stará odpověď ze cache by lhala o tom, co se zrovna děje. Feed a zvuk si
// stejně stahuje čtečka podcastů, ne tahle stránka.
self.addEventListener("install", function () {
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", function (event) {
  event.respondWith(fetch(event.request));
});
