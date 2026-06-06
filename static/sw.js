// Tăng version mỗi khi deploy để force update
const CACHE_VERSION = 'stockai-v3';
const STATIC_CACHE  = ['/static/icon-192.png', '/static/icon-512.png'];

// Install: cache chỉ static assets, KHÔNG cache HTML
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_VERSION)
      .then(c => c.addAll(STATIC_CACHE.filter(u => {
        return fetch(u).then(() => true).catch(() => false);
      })))
      .catch(() => {})
  );
  // Kích hoạt SW mới ngay lập tức
  self.skipWaiting();
});

// Activate: xóa cache cũ
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys
        .filter(k => k !== CACHE_VERSION)
        .map(k => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

// Fetch: Network-first cho HTML/API, cache-first cho static
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Bỏ qua: API calls, non-GET
  if (e.request.method !== 'GET' || url.pathname.startsWith('/api/')) {
    return;
  }

  // Static assets (icons): cache-first
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(e.request).then(r => r || fetch(e.request))
    );
    return;
  }

  // HTML pages (/): Network-first → luôn load bản mới nhất
  e.respondWith(
    fetch(e.request)
      .then(r => r)
      .catch(() => caches.match('/'))
  );
});
