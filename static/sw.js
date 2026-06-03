const CACHE = 'stockai-v1';
const ASSETS = ['/', '/static/icon-192.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)));
});

self.addEventListener('fetch', e => {
  // Only cache GET requests, skip API calls
  if (e.request.method !== 'GET' || e.request.url.includes('/api/')) {
    return;
  }
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});
