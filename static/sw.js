// Service Worker Oficial — Disjoya Autopartes S.A.S. (PWA Trabajador)
const CACHE_NAME = 'disjoya-pwa-v1';

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  // Estrategia Network-First para garantizar sincronización en vivo con Supabase y hora legal NTP
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});
