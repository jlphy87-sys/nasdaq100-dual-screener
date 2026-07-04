/* sw.js — 서비스워커.
 * 앱 셸: cache-first (오프라인 즉시 표시).
 * results.json: network-first (실패 시 캐시).
 *   이유: stale-while-revalidate 는 ⟳ 첫 누름에 옛 캐시를 돌려줘 "한 박자 늦게" 보였음.
 *         즉시 표시는 app.js 의 localStorage 가 이미 담당하므로 SW 이중 캐시 불필요.
 *   비용: 오프라인 첫 응답이 fetch 실패 대기만큼 늦음(캐시 폴백은 유지됨).
 *   탈출구: 아래 fetch 핸들러를 v2 의 cached || network 형태로 되돌리면 됨.
 * 버전 올리면 옛 캐시 정리.
 */
var VERSION = "v8"; /* 관심종목 저장·삭제·추적 탭 (D19, 2026-07-04) */
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

  // results.json → network-first, 실패 시에만 캐시 폴백 (⟳ 누르면 항상 최신)
  if (url.pathname.endsWith("/data/results.json")) {
    e.respondWith(
      caches.open(DATA_CACHE).then(function (cache) {
        return fetch(req).then(function (res) {
          if (res && res.ok) cache.put(req, res.clone());
          return res;
        }).catch(function () {
          return cache.match(req).then(function (cached) {
            if (cached) return cached;
            throw new Error("offline & no cache");
          });
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
