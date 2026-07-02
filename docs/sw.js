/* sw.js — 서비스워커.
 * 앱 셸: cache-first (오프라인 즉시 표시).
 * results.json: stale-while-revalidate (캐시본 먼저 주고, 백그라운드 갱신).
 * 버전 올리면 옛 캐시 정리.
 */
var VERSION = "v2"; /* UI 개선(2026-07-02) — 셸 캐시 무효화 */
var SHELL_CACHE = "shell-" + VERSION;
var DATA_CACHE = "data-" + VERSION;

var SHELL = [
  "./",
  "./index.html",
  "./app.logic.js",
  "./app.js",
  "./styles.css",
  "./manifest.json",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
];

self.addEventListener("install", function (e) {
  self.skipWaiting();
  e.waitUntil(caches.open(SHELL_CACHE).then(function (c) {
    // 개별 실패가 설치를 막지 않도록 best-effort
    return Promise.allSettled(SHELL.map(function (u) { return c.add(u); }));
  }));
});

self.addEventListener("activate", function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (k) {
        if (k !== SHELL_CACHE && k !== DATA_CACHE) return caches.delete(k);
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (e) {
  var req = e.request;
  if (req.method !== "GET") return;
  var url = new URL(req.url);

  // results.json → stale-while-revalidate
  if (url.pathname.endsWith("/data/results.json")) {
    e.respondWith(
      caches.open(DATA_CACHE).then(function (cache) {
        return cache.match(req).then(function (cached) {
          var network = fetch(req).then(function (res) {
            if (res && res.ok) cache.put(req, res.clone());
            return res;
          }).catch(function () { return cached; });
          return cached || network;
        });
      })
    );
    return;
  }

  // 앱 셸 → cache-first, 없으면 네트워크
  if (url.origin === self.location.origin) {
    e.respondWith(
      caches.match(req).then(function (cached) {
        return cached || fetch(req).then(function (res) {
          if (res && res.ok) {
            var copy = res.clone();
            caches.open(SHELL_CACHE).then(function (c) { c.put(req, copy); });
          }
          return res;
        }).catch(function () { return cached; });
      })
    );
  }
});
