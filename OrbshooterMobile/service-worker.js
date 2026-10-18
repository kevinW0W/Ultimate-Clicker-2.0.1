const CACHE_NAME = 'orb-tosser-v5';
const APP_URL = new URL('./Orb-Tosser%20iOS12.html', self.registration.scope).href;
const APP_SHELL = [
    APP_URL,
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

        // iOS 12 standalone apps can reopen the saved launch URL after the
        // network is unavailable or GitHub Pages briefly returns an error.
        // Prefer the pre-cached game shell for navigations so the installed
        // app opens the game instead of showing a GitHub error page.
        if (request.mode === 'navigate') {
            const appShell = await cache.match(APP_URL);
            if (appShell) return appShell;
        }

        if (isSameOrigin && cachedResponse && request.mode !== 'navigate') return cachedResponse;

        try {
            const response = await fetch(request);
            if (response.ok || response.type === 'opaque') {
                const shouldCache = isSameOrigin || request.destination === 'script' || request.destination === 'image';
                if (shouldCache) await cache.put(request, response.clone());
            }
            return response;
        } catch (error) {
            if (cachedResponse) return cachedResponse;
            if (request.mode === 'navigate') {
                const appShell = await cache.match(APP_URL);
                if (appShell) return appShell;
            }
            throw error;
        }
    })());
});
