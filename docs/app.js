/* app.js — DOM 코드만. 순수 로직은 app.logic.js(AppLogic) 재사용. 외부 라이브러리 0.
 * 핵심: "탭 = 이미 끝난 오늘 결과 보기". localStorage 의 마지막 결과를 즉시 렌더하고,
 * 백그라운드로 ./data/results.json 을 재fetch 해 더 최신이면 조용히 갱신 + 토스트.
 * 모든 필드 접근은 방어적 — 깨진/구버전 JSON 이어도 크래시 없이 빈 상태(§10).
 */
(function () {
  "use strict";

  var LS_KEY = "ndx.dual.results.v1";
  var DATA_URL = "./data/results.json";

  // 탭별 정렬 옵션 (명세 §6: S1 기본 거래대금↓, S2 기본 rs_3m↓)
  var SORT_OPTIONS = {
    s1:   [["dollar_volume", "거래대금 ↓"], ["slow_k", "SlowK ↓"], ["name", "종목명"]],
    s2:   [["rs_3m", "RS(3m) ↓"], ["drawdown", "고점대비 ↑"], ["name", "종목명"]],
    both: [["dollar_volume", "거래대금 ↓"], ["rs_3m", "RS(3m) ↓"], ["name", "종목명"]]
  };

  var state = {
    data: null,
    tab: "s1",
    activeSectors: new Set(), // 비어있으면 = 전체
    sort: { s1: "dollar_volume", s2: "rs_3m", both: "dollar_volume" },
    view: "group"
  };

  var fmtMoney = AppLogic.fmtMoney, fmtNum = AppLogic.fmtNum,
      fmtPct = AppLogic.fmtPct, esc = AppLogic.esc, sanitize = AppLogic.sanitize;

  function sectorColor(key) {
    if (state.data) {
      for (var i = 0; i < state.data.sectors.length; i++) {
        if (state.data.sectors[i].key === key) return state.data.sectors[i].color;
      }
    }
    return "#9CA3AF";
  }

  // ---- 데이터 로드: localStorage 즉시 → 네트워크 재검증 ----------------------
  function loadCachedThenRevalidate() {
    try {
      var cached = localStorage.getItem(LS_KEY);
      if (cached) setData(JSON.parse(cached), false);
    } catch (e) { /* 손상 캐시 무시 */ }
    revalidate(true);
  }

  // 결과 문자열("updated"|"same"|"error")로 resolve — 수동 새로고침 UI가 완료 시점·결과에 반응.
  // .finally 는 테스트의 동기 thenable 스텁이 미지원이라 then/catch 만 사용.
  function revalidate(allowToast) {
    return fetch(DATA_URL, { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (json) {
        var clean = sanitize(json);
        if (!clean) throw new Error("형식 오류");
        var prev = state.data;
        var isNewer = !prev || !prev.as_of || (clean.as_of && clean.as_of > prev.as_of) ||
          (clean.generated_at && prev.generated_at && clean.generated_at !== prev.generated_at);
        setData(clean, false);
        try { localStorage.setItem(LS_KEY, JSON.stringify(json)); } catch (e) {}
        hideError();
        if (allowToast && prev && isNewer) showToast("업데이트됨");
        return isNewer ? "updated" : "same";
      })
      .catch(function (err) {
        // 네트워크/형식 실패: 캐시가 있으면 그대로 두고, 없으면 안내
        if (!state.data) showError("결과를 불러오지 못했습니다. 연결을 확인하고 새로고침하세요.");
        return "error";
      });
  }

  // 수동 새로고침(버튼·당김) 공통: 실제 완료까지 스피너 유지(최소 minMs — 깜빡임 방지),
  // 변화가 없어도 반드시 피드백을 준다 ("눌렀는데 반응 없음" 방지).
  function manualRefresh(minMs, done) {
    var started = Date.now();
    revalidate(true).then(function (result) {
      var wait = Math.max(0, minMs - (Date.now() - started));
      setTimeout(function () {
        done();
        if (result === "same") showToast("이미 최신입니다");
        else if (result === "error" && state.data) showToast("연결 실패 — 마지막 결과 표시 중");
      }, wait);
    });
  }

  function setData(raw, persist) {
    var clean = sanitize(raw);   // 항상 정규화(불신): 캐시본/네트워크본 모두 안전화
    if (!clean) { showError("데이터 형식이 올바르지 않습니다."); return; }
    state.data = clean;
    if (persist) { try { localStorage.setItem(LS_KEY, JSON.stringify(raw)); } catch (e) {} }
    renderAll();
  }

  // ---- 렌더 -----------------------------------------------------------------
  function renderAll() {
    renderHeader(); renderTabs(); renderRegimeBanner();
    renderSortOptions(); renderChips(); renderContent(); renderFooter();
  }

  function renderHeader() {
    var d = state.data;
    document.getElementById("as-of").textContent = d.as_of || "—";
    document.getElementById("universe-count").textContent =
      d.universe_count == null ? "—" : d.universe_count;
    document.getElementById("stale-banner").classList.toggle("hidden", !d.stale);
  }

  function renderTabs() {
    var c = state.data.counts;
    document.getElementById("count-s1").textContent = c.s1;
    document.getElementById("count-s2").textContent = c.s2;
    document.getElementById("count-both").textContent = c.both;
    ["s1", "s2", "both"].forEach(function (t) {
      document.getElementById("tab-" + t).classList.toggle("active", state.tab === t);
    });
  }

  // D13: regime.ok=false 는 에러가 아니라 정상 동작(오늘 진입 없음) → 정보색 배너.
  // ok=null 은 QQQ 실패 = 판정불가 → 다른 문구(경고 톤이지만 스크린 자체는 유효).
  function renderRegimeBanner() {
    var b = document.getElementById("regime-banner");
    var r = state.data.regime;
    var showTab = state.tab === "s2" || state.tab === "both";
    if (showTab && r.enabled && r.ok === false) {
      b.textContent = "시장 체제 필터: QQQ가 200일선 아래 — 오늘 진입 없음 (스크린이 일한 결과입니다)";
      b.classList.remove("hidden");
    } else if (showTab && r.enabled && r.ok === null) {
      b.textContent = "QQQ 데이터를 얻지 못해 스크리닝2를 판정할 수 없습니다 (판정불가 — 통과 아님)";
      b.classList.remove("hidden");
    } else {
      b.classList.add("hidden");
    }
  }

  function renderSortOptions() {
    var sel = document.getElementById("sort-select");
    var opts = SORT_OPTIONS[state.tab];
    var html = "";
    for (var i = 0; i < opts.length; i++) {
      html += '<option value="' + opts[i][0] + '">' + opts[i][1] + "</option>";
    }
    sel.innerHTML = html;
    sel.value = state.sort[state.tab];
  }

  function tabItems() {
    return AppLogic.itemsForTab(state.data.items, state.tab);
  }

  function renderChips() {
    var box = document.getElementById("sector-chips");
    var items = tabItems();
    // 칩 개수는 '현재 탭' 기준으로 재계산 → 리스트와 항상 일치
    var perSector = {}, total = items.length;
    items.forEach(function (it) {
      perSector[it.sector_kr] = (perSector[it.sector_kr] || 0) + 1;
    });
    var html = chipHtml("__all__", "전체", "var(--accent)", total, state.activeSectors.size === 0);
    var order = state.data.sectors.map(function (s) { return s.key; });
    Object.keys(perSector).sort(function (a, b) {
      var ia = order.indexOf(a), ib = order.indexOf(b);
      return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
    }).forEach(function (key) {
      html += chipHtml(key, key, sectorColor(key), perSector[key], state.activeSectors.has(key));
    });
    box.innerHTML = html;
  }

  function chipHtml(key, label, color, count, active) {
    var style = active ? ' style="background:' + esc(color) + '"' : "";
    var dot = key === "__all__" ? "" : '<span class="dot" style="background:' + esc(color) + '"></span>';
    return '<button class="chip' + (active ? " active" : "") + '" data-sector="' + esc(key) + '"' + style + ">" +
      dot + "<span>" + esc(label) + '</span><span class="cnt">' + count + "</span></button>";
  }

  function renderContent() {
    var el = document.getElementById("content");
    var r = state.data.regime;
    // S2/겹침 탭 + 체제 미통과/판정불가 → 리스트 대신 안내 (배너와 중복이지만 본문에도)
    if ((state.tab === "s2" || state.tab === "both") && r.enabled && r.ok !== true) {
      el.innerHTML = '<div class="empty"><div class="icon">' +
        (r.ok === false ? "🛡️" : "📡") + '</div><div class="big">' +
        (r.ok === false ? "시장 체제 필터: 오늘 진입 없음" : "스크리닝2 판정불가 (QQQ 데이터 없음)") +
        '</div><div class="small">' +
        (r.ok === false
          ? "QQQ " + fmtNum(r.qqq_close, 2) + " < 200일선 " + fmtNum(r.qqq_sma200, 2)
          : "다음 자동 갱신을 기다리거나 새로고침하세요.") +
        "</div></div>";
      return;
    }
    var items = AppLogic.filterSort(tabItems(),
      Array.from(state.activeSectors), state.sort[state.tab]);
    if (items.length === 0) {
      el.innerHTML = '<div class="empty"><div class="icon">🔍</div><div class="big">' +
        (tabItems().length === 0
          ? "오늘 조건을 만족하는 종목이 없습니다."
          : "선택한 섹터에 해당 종목이 없습니다.") +
        '</div>' + (state.tab === "s2"
          ? '<div class="small">스크리닝2는 4층 필터라 0~수 개가 정상입니다.</div>' : "") +
        "</div>";
      return;
    }
    el.innerHTML = state.view === "flat" ? renderFlat(items) : renderGroup(items);
  }

  function renderFlat(items) {
    var out = "";
    for (var i = 0; i < items.length; i++) out += cardHtml(items[i]);
    return out;
  }

  function renderGroup(items) {
    var order = state.data.sectors.map(function (s) { return s.key; });
    var groups = AppLogic.groupBySector(items, order);
    return groups.map(function (g) {
      var color = sectorColor(g.key);
      return '<section class="sector-group"><div class="sector-head">' +
        '<span class="swatch" style="background:' + esc(color) + '"></span>' +
        "<span>" + esc(g.key) + '</span><span class="n">' + g.items.length + "종목</span></div>" +
        g.items.map(cardHtml).join("") + "</section>";
    }).join("");
  }

  function cardHtml(it) {
    var color = sectorColor(it.sector_kr);
    var both = it.pass_s1 && it.pass_s2;
    var badge = both ? '<span class="badge both">S1·S2</span>'
      : (it.pass_s1 ? '<span class="badge s1">S1</span>' : '<span class="badge s2">S2</span>');

    var row2 = "";
    var showS1 = (state.tab === "s1" || state.tab === "both") && it.s1;
    var showS2 = (state.tab === "s2" || state.tab === "both") && it.s2;
    if (showS1) {
      row2 += '<div class="card-row2">' +
        kv("골든크로스", esc(it.s1.gc_date || "—")) +
        kv("SlowK", fmtNum(it.s1.slow_k, 1)) +
        kv("거래대금", fmtMoney(it.s1.avg_dollar_volume)) +
        "</div>";
    }
    if (showS2) {
      row2 += '<div class="card-row2">' +
        kv("RS(3m)", fmtNum(it.s2.rs_3m, 2) + "×") +
        kv("고점대비", fmtPct(it.s2.drawdown)) +
        kv("트리거", esc(it.s2.trigger || "—")) +
        "</div>";
    }

    // 확장: 미니 차트(있으면) + 해당 스크린 전체 사용값 + (both 면) 반대쪽 값도 (명세 §8)
    var detail = it.chart ? chartHtml(it.chart) : "";
    if (it.s1 && (showS1 || both)) {
      detail += '<div class="detail-title">S1 · 반전 초기</div><div class="card-detail">' +
        dv("MACD", fmtNum(it.s1.macd, 4)) + dv("Signal", fmtNum(it.s1.signal, 4)) +
        dv("Hist", fmtNum(it.s1.hist, 4)) + dv("SlowK", fmtNum(it.s1.slow_k, 2)) +
        dv("SlowD", fmtNum(it.s1.slow_d, 2)) + dv("GC일", esc(it.s1.gc_date || "—")) +
        dv("거래대금", fmtMoney(it.s1.avg_dollar_volume)) +
        dv("SMA200", fmtNum(it.s1.sma200, 2)) + dv("Vol비율", fmtNum(it.s1.vol_ratio, 2)) +
        dv("ADX", fmtNum(it.s1.adx, 1)) + "</div>";
    }
    if (it.s2 && (showS2 || both)) {
      detail += '<div class="detail-title">S2 · 추세 눌림목</div><div class="card-detail">' +
        dv("RS 3m", fmtNum(it.s2.rs_3m, 3)) + dv("RS 6m", fmtNum(it.s2.rs_6m, 3)) +
        dv("SMA50", fmtNum(it.s2.sma50, 2)) + dv("SMA200", fmtNum(it.s2.sma200, 2)) +
        dv("고점대비", fmtPct(it.s2.drawdown)) + dv("눌림저점", fmtPct(it.s2.pullback_low_pct)) +
        dv("트리거", esc(it.s2.trigger || "—")) + dv("Vol비율", fmtNum(it.s2.vol_ratio, 2)) +
        "</div>";
    }

    return '<article class="card" style="border-left-color:' + esc(color) + '">' +
      '<div class="card-row1"><span class="tk">' + esc(it.ticker) + "</span>" +
      '<span class="dot" style="background:' + esc(color) + '"></span>' +
      '<span class="nm">' + esc(it.name) + "</span>" +
      '<span class="price">' + (it.price == null ? "—" : "$" + it.price.toLocaleString()) + "</span>" +
      '<span class="badges">' + badge + '</span><span class="chev">▼</span></div>' +
      row2 + detail + "</article>";
  }
  // 종가 시리즈 → 인라인 SVG 라인차트 (라이브러리 0, 오프라인 동작).
  // 데이터는 sanitizeChart 를 통과한 것만 온다 (양수 숫자 2~260개 보장).
  function chartHtml(ch) {
    var c = ch.closes, n = c.length;
    var W = 320, H = 84, TOP = 6, BOT = 6;
    var min = Math.min.apply(null, c), max = Math.max.apply(null, c);
    var span = (max - min) || 1;
    var pts = [];
    for (var i = 0; i < n; i++) {
      var x = (i / (n - 1)) * W;
      var y = TOP + (1 - (c[i] - min) / span) * (H - TOP - BOT);
      pts.push(x.toFixed(1) + "," + y.toFixed(1));
    }
    var chg = c[n - 1] / c[0] - 1;
    var cls = chg >= 0 ? "pos" : "neg";
    var line = pts.join(" ");
    var area = "0," + H + " " + line + " " + W + "," + H;
    return '<div class="chart-wrap ' + cls + '">' +
      '<svg class="spark" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none" aria-hidden="true">' +
      '<polygon class="spark-area" points="' + area + '"/>' +
      '<polyline class="spark-line" fill="none" points="' + line + '"/></svg>' +
      '<div class="chart-cap"><span>' + esc(ch.start || "") + " ~ " + esc(ch.end || "") + " · " + n + '봉</span>' +
      '<span>저 ' + fmtNum(min, 2) + " · 고 " + fmtNum(max, 2) +
      ' · <b class="' + cls + '">' + (chg >= 0 ? "+" : "") + fmtPct(chg) + "</b></span></div></div>";
  }

  function kv(k, v) { return '<span class="kv"><span class="k">' + k + '</span><br><span class="v">' + v + "</span></span>"; }
  function dv(k, v) { return '<span><span class="k">' + k + '</span><br><span class="v">' + v + "</span></span>"; }

  function renderFooter() {
    var d = state.data;
    var cs = d.config_summary || {};
    var summary = state.tab === "s2" ? cs.s2 : cs.s1;
    document.getElementById("meta-line").innerHTML =
      "데이터 yfinance · 생성 " + esc(d.generated_at || "—") +
      " · 스킵 " + (d.errors_count == null ? "—" : d.errors_count) +
      (summary ? " · " + esc(summary) : "");
  }

  // ---- UI 상태 ----------------------------------------------------------------
  function showToast(msg) {
    var t = document.getElementById("toast");
    t.textContent = msg; t.classList.remove("hidden");
    clearTimeout(showToast._t);
    showToast._t = setTimeout(function () { t.classList.add("hidden"); }, 2200);
  }
  function showError(msg) {
    var b = document.getElementById("error-banner");
    b.textContent = "⚠ " + msg; b.classList.remove("hidden");
    if (!state.data) document.getElementById("content").innerHTML =
      '<div class="empty"><div class="big">' + esc(msg) + "</div></div>";
  }
  function hideError() { document.getElementById("error-banner").classList.add("hidden"); }

  // ---- 이벤트 ----------------------------------------------------------------
  function setTab(tab) {
    state.tab = tab;
    if (state.data) {
      renderTabs(); renderRegimeBanner(); renderSortOptions(); renderChips(); renderContent(); renderFooter();
    }
  }

  function wire() {
    ["s1", "s2", "both"].forEach(function (t) {
      document.getElementById("tab-" + t).addEventListener("click", function () { setTab(t); });
    });
    document.getElementById("refresh-btn").addEventListener("click", function () {
      var btn = document.getElementById("refresh-btn");
      if (btn.classList.contains("spinning")) return; // 연타 방지
      btn.classList.add("spinning");
      manualRefresh(500, function () { btn.classList.remove("spinning"); });
    });
    document.getElementById("sort-select").addEventListener("change", function (e) {
      state.sort[state.tab] = e.target.value;
      if (state.data) renderContent();
    });
    document.getElementById("view-group").addEventListener("click", function () { setView("group"); });
    document.getElementById("view-flat").addEventListener("click", function () { setView("flat"); });

    document.getElementById("sector-chips").addEventListener("click", function (e) {
      var btn = e.target.closest(".chip"); if (!btn) return;
      var key = btn.getAttribute("data-sector");
      if (key === "__all__") state.activeSectors.clear();
      else if (state.activeSectors.has(key)) state.activeSectors.delete(key);
      else state.activeSectors.add(key);
      renderChips(); renderContent();
    });

    document.getElementById("content").addEventListener("click", function (e) {
      var card = e.target.closest(".card"); if (card) card.classList.toggle("open");
    });

    setupPullToRefresh();
  }

  function setView(v) {
    state.view = v;
    document.getElementById("view-group").classList.toggle("active", v === "group");
    document.getElementById("view-flat").classList.toggle("active", v === "flat");
    if (state.data) renderContent();
  }

  function spinPtr(on) {
    var p = document.getElementById("ptr");
    p.style.height = on ? "40px" : "0";
    p.classList.toggle("spin", on);
  }

  function setupPullToRefresh() {
    var startY = 0, pulling = false;
    window.addEventListener("touchstart", function (e) {
      if (window.scrollY <= 0) { startY = e.touches[0].clientY; pulling = true; }
    }, { passive: true });
    window.addEventListener("touchmove", function (e) {
      if (!pulling) return;
      var dy = e.touches[0].clientY - startY;
      if (dy > 0) document.getElementById("ptr").style.height = Math.min(dy / 2, 50) + "px";
    }, { passive: true });
    window.addEventListener("touchend", function (e) {
      if (!pulling) return;
      var dy = (e.changedTouches[0].clientY - startY);
      pulling = false;
      if (dy > 90) { spinPtr(true); manualRefresh(600, function () { spinPtr(false); }); }
      else spinPtr(false);
    });
  }

  // ---- 시작 ------------------------------------------------------------------
  function init() {
    wire();
    loadCachedThenRevalidate();
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("./sw.js").catch(function () {});
    }
  }
  document.addEventListener("DOMContentLoaded", init);
})();
