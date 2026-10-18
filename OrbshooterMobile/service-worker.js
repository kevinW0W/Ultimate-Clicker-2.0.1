const CACHE_NAME = 'orb-tosser-v19';
const APP_URL = new URL('./', self.registration.scope).href;
const IOS12_PAGE_URL = new URL('./index.html', self.registration.scope).href;
const IOS12_MANIFEST_URL = new URL('./manifest.webmanifest', self.registration.scope).href;
const APP_SHELL = [
    APP_URL,
    IOS12_PAGE_URL,
    IOS12_MANIFEST_URL,
    new URL('./Engine%20Ai%20Bots.js', self.registration.scope).href,
    new URL('./manifest.webmanifest', self.registration.scope).href,
    new URL('./icon.ico', self.registration.scope).href,
    new URL('./icon.png', self.registration.scope).href,
    new URL('./touchicon.png', self.registration.scope).href
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => cache.addAll(APP_SHELL))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((cacheNames) => Promise.all(
            cacheNames
                .filter((cacheName) => cacheName.startsWith('orb-tosser-') && cacheName !== CACHE_NAME)
                .map((cacheName) => caches.delete(cacheName))
        )).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const request = event.request;
    if (request.method !== 'GET') return;

    const isSameOrigin = new URL(request.url).origin === self.location.origin;
    event.respondWith((async () => {
        const cache = await caches.open(CACHE_NAME);
        const cachedResponse = await cache.match(request);

        if (isSameOrigin && cachedResponse && request.mode !== 'navigate') return cachedResponse;

        try {
            const response = await fetch(request);
            if (response.ok || response.type === 'opaque') {
                const shouldCache = isSameOrigin || request.destination === 'script' || request.destination === 'image';
                if (shouldCache) await cache.put(request, response.clone());
            }
            if (request.mode === 'navigate' && !response.ok) {
                const appShell = await cache.match(request) ||
                    (new URL(request.url).pathname === new URL(IOS12_PAGE_URL).pathname
                        ? await cache.match(IOS12_PAGE_URL)
                        : await cache.match(APP_URL));
                if (appShell) return appShell;
            }
            return response;
        } catch (error) {
            if (cachedResponse) return cachedResponse;
            if (request.mode === 'navigate') {
                const appShell = new URL(request.url).pathname === new URL(IOS12_PAGE_URL).pathname
                    ? await cache.match(IOS12_PAGE_URL)
                    : await cache.match(APP_URL);
                if (appShell) return appShell;
            }
            throw error;
        }
    })());
});
