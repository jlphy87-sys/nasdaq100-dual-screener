/* app.js — DOM 코드만. 순수 로직은 app.logic.js(AppLogic) 재사용. 외부 라이브러리 0.
 * 핵심: "탭 = 이미 끝난 오늘 결과 보기". localStorage 의 마지막 결과를 즉시 렌더하고,
 * 백그라운드로 ./data/results.json 을 재fetch 해 더 최신이면 조용히 갱신 + 토스트.
 * 모든 필드 접근은 방어적 — 깨진/구버전 JSON 이어도 크래시 없이 빈 상태(§10).
 */
(function () {
  "use strict";

  var LS_KEY = "ndx.dual.results.v1";
  var WATCH_KEY = "ndx.dual.watch.v1";   // D19: 관심종목은 폰 로컬 소유
  var DATA_URL = "./data/results.json";

  // 탭별 정렬 옵션 (명세 §6: S1 기본 거래대금↓, S2 기본 rs_3m↓)
  var SORT_OPTIONS = {
    s1:    [["dollar_volume", "거래대금 ↓"], ["slow_k", "SlowK ↓"], ["name", "종목명"]],
    s2:    [["rs_3m", "RS(3m) ↓"], ["drawdown", "고점대비 ↑"], ["name", "종목명"]],
    both:  [["dollar_volume", "거래대금 ↓"], ["rs_3m", "RS(3m) ↓"], ["name", "종목명"]],
    watch: [["saved_at", "저장일 ↓"], ["ret", "저장후 수익률 ↓"], ["name", "종목명"]]
  };

  var state = {
    data: null,
    tab: "s1",
    activeSectors: new Set(), // 비어있으면 = 전체
    sort: { s1: "dollar_volume", s2: "rs_3m", both: "dollar_volume", watch: "saved_at" },
    view: "group",
    watch: [],                // D19: sanitizeWatch 통과분만 유지
    tradeInput: null          // D20b: 열려 있는 가격 입력폼 {ticker, kind:"buy"|"sell"}
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

  // ---- 관심종목 (D19: 저장·삭제·저장일 기록) ---------------------------------
  // 저장일은 사용자 '행동'의 시각이므로 기기 날짜가 원천 (D7 의 as_of 재조립과
  // 다른 범주). 데이터 기준일은 saved_as_of 로 따로 남겨 수익률 기준을 명시.
  function todayStr() {
    var d = new Date();
    var m = d.getMonth() + 1, day = d.getDate();
    return d.getFullYear() + "-" + (m < 10 ? "0" : "") + m + "-" + (day < 10 ? "0" : "") + day;
  }

  function loadWatch() {
    try {
      state.watch = AppLogic.sanitizeWatch(JSON.parse(localStorage.getItem(WATCH_KEY) || "[]"));
    } catch (e) { state.watch = []; }  // 손상 저장분 → 빈 목록 (크래시 금지)
  }

  function persistWatch() {
    try { localStorage.setItem(WATCH_KEY, JSON.stringify(state.watch)); } catch (e) {}
  }

  function isWatched(tk) {
    for (var i = 0; i < state.watch.length; i++) {
      if (state.watch[i].ticker === tk) return true;
    }
    return false;
  }

  function toggleWatch(tk) {
    if (!tk) return;
    if (isWatched(tk)) { removeWatch(tk); return; }
    var it = null;
    if (state.data) {
      for (var i = 0; i < state.data.items.length; i++) {
        if (state.data.items[i].ticker === tk) { it = state.data.items[i]; break; }
      }
    }
    if (!it) return;
    state.watch.unshift({
      ticker: it.ticker, name: it.name, sector_kr: it.sector_kr,
      saved_at: todayStr(),
      saved_as_of: state.data.as_of,
      saved_price: it.price
    });
    state.watch = AppLogic.sanitizeWatch(state.watch);  // 상한·중복 방어 일원화
    persistWatch();
    showToast("★ 저장됨 — 관심 탭에서 추적");
    renderTabs(); renderContent();
  }

  function removeWatch(tk) {
    state.watch = state.watch.filter(function (e) { return e.ticker !== tk; });
    persistWatch();
    showToast("관심종목에서 삭제됨");
    renderTabs(); renderContent();
  }

  // ---- 가상 매매 표식 (D20: 매수/매도 마킹 + 손익) ----------------------------
  // 가격은 그날 서버 시세(일봉 종가)로 자동 기록 — 일봉 기반이라 장중 체결가
  // 개념이 없고, WebView 는 prompt() 미지원이라 입력폼 없이 단순하게 간다.
  // 한계: 사이클 1개만 보관(다시 매수 시 이전 기록 덮어씀). 탈출구: 이력이
  // 필요해지면 entry.trades[] 배열로 확장 (스키마 추가만이라 호환).
  function findWatch(tk) {
    for (var i = 0; i < state.watch.length; i++) {
      if (state.watch[i].ticker === tk) return state.watch[i];
    }
    return null;
  }

  function curPriceOf(tk) {
    if (!state.data) return null;
    for (var i = 0; i < state.data.items.length; i++) {
      var it = state.data.items[i];
      if (it.ticker === tk && it.price != null) return it.price;
    }
    var q = state.data.quotes ? state.data.quotes[tk] : null;
    return q ? q.price : null;
  }

  // D20b: 버튼 클릭 → 즉시 기록이 아니라 가격 입력폼(현재 종가 기본값) 오픈.
  // 사용자가 실제 체결가로 고쳐 기록할 수 있다 (WebView prompt 미지원 → 인라인 폼).
  function openTradeInput(tk, kind) {
    if (!findWatch(tk)) return;
    if (kind === "sell") {
      var en = findWatch(tk);
      if (!en || en.buy_price == null) return;   // 매수 없는 매도 금지
    }
    state.tradeInput = { ticker: tk, kind: kind };
    renderContent();
  }

  function confirmTradeInput() {
    var ti = state.tradeInput;
    if (!ti) return;
    var inp = document.getElementById("tp-" + ti.ticker);
    var p = AppLogic.parsePrice(inp ? inp.value : null);
    if (p == null) { showToast("가격을 확인해 주세요 (양수 숫자)"); return; } // 폼 유지
    var en = findWatch(ti.ticker);
    if (!en) { state.tradeInput = null; renderContent(); return; }
    if (ti.kind === "buy") {
      // D20c: 완결 사이클은 이력으로 보존 후 새 사이클 시작 (덮어쓰기 금지)
      if (en.buy_price != null && en.sell_price != null) {
        en.trades = en.trades || [];
        en.trades.push({ buy_price: en.buy_price, buy_at: en.buy_at,
                         sell_price: en.sell_price, sell_at: en.sell_at });
      }
      en.buy_price = p; en.buy_at = todayStr();
      en.sell_price = null; en.sell_at = null;
      showToast("매수 표시 $" + p.toLocaleString() + " 기록됨");
    } else {
      if (en.buy_price == null) { state.tradeInput = null; renderContent(); return; }
      en.sell_price = p; en.sell_at = todayStr();
      var ret = p / en.buy_price - 1;
      showToast("매도 표시 — 확정 " + (ret >= 0 ? "+" : "") + fmtPct(ret));
    }
    state.tradeInput = null;
    persistWatch();
    renderContent();
  }

  function cancelTradeInput() {
    state.tradeInput = null;
    renderContent();
  }

  // D20c: 매매 기록 삭제 — i>=0 은 이력(trades) 행, -1 은 현재 사이클.
  // 관심종목 저장 자체는 유지된다 (그건 ★/[삭제] 담당).
  function deleteTrade(tk, i) {
    var en = findWatch(tk);
    if (!en) return;
    if (i >= 0 && en.trades && i < en.trades.length) en.trades.splice(i, 1);
    else { en.buy_price = null; en.buy_at = null; en.sell_price = null; en.sell_at = null; }
    persistWatch();
    showToast("매매 기록 삭제됨");
    renderContent();
  }

  // 차트에 얹을 매수/매도 가격선 — 어느 탭이든 관심 저장분이 있으면 표시
  function tradeMarks(tk) {
    var en = findWatch(tk);
    if (!en || en.buy_price == null) return null;
    return { buy: en.buy_price, sell: en.sell_price };
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
    document.getElementById("count-watch").textContent = state.watch.length;
    ["s1", "s2", "both", "watch"].forEach(function (t) {
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
    if (state.tab === "watch") { box.innerHTML = ""; return; }  // 관심 탭은 단일 목록
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
    if (state.tab === "watch") { el.innerHTML = renderWatch(); return; }
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
        // map(cardHtml) 금지: map 이 넘기는 index 가 2번째 인자(관심 스냅샷)로 오인됨
        g.items.map(function (x) { return cardHtml(x); }).join("") + "</section>";
    }).join("");
  }

  // wen(관심 스냅샷)이 오면 저장일·저장가·저장후 수익률 행을 덧붙인다 (관심 탭 전용)
  function cardHtml(it, wen) {
    var color = sectorColor(it.sector_kr);
    var both = it.pass_s1 && it.pass_s2;
    var badge = both ? '<span class="badge both">S1·S2</span>'
      : (it.pass_s1 ? '<span class="badge s1">S1</span>' : '<span class="badge s2">S2</span>');
    var watched = isWatched(it.ticker);
    var star = '<button class="star' + (watched ? " on" : "") + '" data-tk="' + esc(it.ticker) +
      '" aria-label="관심종목 저장/삭제">' + (watched ? "★" : "☆") + "</button>";

    var row2 = "";
    var showS1 = (state.tab === "s1" || state.tab === "both" || state.tab === "watch") && it.s1;
    var showS2 = (state.tab === "s2" || state.tab === "both" || state.tab === "watch") && it.s2;
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
    var detail = it.chart ? chartHtml(it.chart, tradeMarks(it.ticker)) : "";
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

    var watchRow = "";
    if (wen && wen.ticker) {
      var ret = AppLogic.watchReturn(wen, it.price);
      watchRow = '<div class="watch-row"><span>저장 ' + esc(wen.saved_at || "—") +
        (wen.saved_price != null ? " · $" + wen.saved_price.toLocaleString() : "") + "</span><span>" +
        (ret == null ? "" : '이후 <b class="' + (ret >= 0 ? "pos" : "neg") + '">' +
          (ret >= 0 ? "+" : "") + fmtPct(ret) + "</b> ") +
        '<button class="watch-del" data-tk="' + esc(wen.ticker) + '">삭제</button></span></div>' +
        tradeHtml(wen, it.price);
    }

    return '<article class="card" style="border-left-color:' + esc(color) + '">' +
      '<div class="card-row1"><span class="tk">' + esc(it.ticker) + "</span>" +
      '<span class="dot" style="background:' + esc(color) + '"></span>' +
      '<span class="nm">' + esc(it.name) + "</span>" +
      '<span class="price">' + (it.price == null ? "—" : "$" + it.price.toLocaleString()) + "</span>" +
      '<span class="badges">' + badge + "</span>" + star + '<span class="chev">▼</span></div>' +
      watchRow + row2 + detail + "</article>";
  }

  // ---- 관심 탭 렌더 (D19) ----------------------------------------------------
  function renderWatch() {
    if (state.watch.length === 0) {
      return '<div class="empty"><div class="icon">☆</div>' +
        '<div class="big">저장한 종목이 없습니다.</div>' +
        '<div class="small">카드 오른쪽의 ☆ 를 누르면 여기에 저장되고,<br>' +
        "저장일·저장가 대비 수익률로 계속 추적할 수 있습니다.</div></div>";
    }
    var quotes = state.data.quotes || {};
    var liveMap = {};
    for (var i = 0; i < state.data.items.length; i++) {
      liveMap[state.data.items[i].ticker] = state.data.items[i];
    }
    var prices = {};
    for (var j = 0; j < state.watch.length; j++) {
      var tk = state.watch[j].ticker;
      var live = liveMap[tk];
      prices[tk] = (live && live.price != null) ? live.price
        : (quotes[tk] ? quotes[tk].price : null);
    }
    var entries = AppLogic.sortWatch(state.watch, prices, state.sort.watch);
    var out = tradeLogHtml(prices);   // D20c: 매매 기록 표 (기록 있을 때만)
    for (var k = 0; k < entries.length; k++) {
      var en = entries[k];
      // 오늘 스크리닝에 있으면 정식 카드(차트 포함) + 관심 행, 없으면 시세 추적 카드
      out += liveMap[en.ticker]
        ? cardHtml(liveMap[en.ticker], en)
        : watchCardHtml(en, quotes[en.ticker] || null);
    }
    return out;
  }

  // 오늘 결과에 없는 저장 종목: quotes 시세로 추적 (구버전 JSON 이면 "시세 없음")
  function watchCardHtml(en, q) {
    var color = sectorColor(en.sector_kr);
    var cur = q ? q.price : null;
    var chg = q ? q.chg : null;
    var ret = AppLogic.watchReturn(en, cur);
    // D21: 전 유니버스 charts 에서 봉차트 — 카드를 누르면 펼쳐진다.
    // 구버전 JSON(charts 없음)이면 차트만 조용히 생략(▼ 표시도 숨김).
    var ch = (state.data && state.data.charts) ? state.data.charts[en.ticker] : null;
    var detail = ch ? chartHtml(ch, tradeMarks(en.ticker)) : "";
    return '<article class="card watch-card" style="border-left-color:' + esc(color) + '">' +
      '<div class="card-row1"><span class="tk">' + esc(en.ticker) + "</span>" +
      '<span class="dot" style="background:' + esc(color) + '"></span>' +
      '<span class="nm">' + esc(en.name) + "</span>" +
      '<span class="price">' + (cur == null ? "—" : "$" + cur.toLocaleString()) + "</span>" +
      '<span class="badges"><span class="badge watch">추적</span></span>' +
      '<button class="star on" data-tk="' + esc(en.ticker) + '" aria-label="관심종목 삭제">★</button>' +
      (ch ? '<span class="chev">▼</span>' : "") + "</div>" +
      '<div class="watch-row"><span>저장 ' + esc(en.saved_at || "—") +
      (en.saved_price != null ? " · $" + en.saved_price.toLocaleString() : "") + "</span><span>" +
      (chg == null ? "" : '오늘 <b class="' + (chg >= 0 ? "pos" : "neg") + '">' +
        (chg >= 0 ? "+" : "") + fmtPct(chg) + "</b> · ") +
      (ret != null ? '이후 <b class="' + (ret >= 0 ? "pos" : "neg") + '">' +
        (ret >= 0 ? "+" : "") + fmtPct(ret) + "</b> "
        : (cur == null ? "시세 없음 " : "")) +
      '<button class="watch-del" data-tk="' + esc(en.ticker) + '">삭제</button></span></div>' +
      tradeHtml(en, cur) + detail +
      '<div class="watch-note">오늘 스크리닝 목록에는 없음 — 시세로만 추적 중</div></article>';
  }

  // 가상 매매 행 (D20): 표식(매수/매도 가격·날짜) + 평가/확정 손익
  function tradeHtml(en, cur) {
    var ti = state.tradeInput;
    if (ti && ti.ticker === en.ticker) {   // 가격 입력폼 (기본값 = 현재 종가)
      var label = ti.kind === "buy" ? "매수가" : "매도가";
      return '<div class="trade-row trade-form"><span>' + label + ' $' +
        '<input id="tp-' + esc(en.ticker) + '" class="trade-price" type="text" inputmode="decimal"' +
        ' value="' + (cur == null ? "" : cur) + '" aria-label="' + label + '"></span><span>' +
        '<button class="trade-ok">기록</button>' +
        '<button class="trade-cancel">취소</button></span></div>';
    }
    var tr = AppLogic.tradeReturn(en, cur);
    if (!tr) {
      return '<div class="trade-row"><span class="trade-hint">가상 매매 — 기록 없음</span>' +
        '<button class="trade-buy" data-tk="' + esc(en.ticker) + '">매수 표시</button></div>';
    }
    var retHtml = tr.ret == null ? "시세 없음"
      : '<b class="' + (tr.ret >= 0 ? "pos" : "neg") + '">' +
        (tr.ret >= 0 ? "+" : "") + fmtPct(tr.ret) + "</b>";
    var clear = '<button class="trade-clear" data-tk="' + esc(en.ticker) +
      '" aria-label="매매 기록 삭제">✕</button>';
    if (tr.closed) {
      return '<div class="trade-row"><span><i class="mark buy">매수</i>$' +
        en.buy_price.toLocaleString() + ' <i class="mark sell">매도</i>$' +
        en.sell_price.toLocaleString() + " · " + esc(en.sell_at || "") + "</span>" +
        "<span>확정 " + retHtml +
        ' <button class="trade-buy" data-tk="' + esc(en.ticker) + '">다시 매수</button>' +
        clear + "</span></div>";
    }
    return '<div class="trade-row"><span><i class="mark buy">매수</i>$' +
      en.buy_price.toLocaleString() + " · " + esc(en.buy_at || "") + "</span>" +
      "<span>평가 " + retHtml +
      ' <button class="trade-sell" data-tk="' + esc(en.ticker) + '">매도 표시</button>' +
      clear + "</span></div>";
  }

  // D20c: 매매 기록 표 — 관심 탭 상단, 이력 + 현재 사이클 한눈에
  function tradeLogHtml(prices) {
    var rows = AppLogic.tradeRows(state.watch, prices, todayStr());
    if (!rows.length) return "";
    var body = "";
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      var days = r.days == null ? "—" : (r.days === 0 ? "당일" : r.days + "일");
      body += '<tr><td class="tk2">' + esc(r.ticker) + "</td>" +
        "<td>$" + r.buy_price.toLocaleString() +
        '<span class="d">' + esc(r.buy_at || "") + "</span></td>" +
        "<td>" + (r.sell_price != null
          ? "$" + r.sell_price.toLocaleString() + '<span class="d">' + esc(r.sell_at || "") + "</span>"
          : '<span class="hold">보유중</span>') + "</td>" +
        '<td class="days">' + days + "</td>" +
        "<td>" + (r.ret == null ? "—"
          : '<b class="' + (r.ret >= 0 ? "pos" : "neg") + '">' +
            (r.ret >= 0 ? "+" : "") + fmtPct(r.ret) + "</b>") +
        (r.closed ? "" : '<span class="d">평가</span>') + "</td>" +
        '<td><button class="tl-del" data-tk="' + esc(r.ticker) + '" data-i="' + r.i +
        '" aria-label="기록 삭제">✕</button></td></tr>';
    }
    var sum = "";
    var closed = [], wins = 0, acc = 0;
    for (var j = 0; j < rows.length; j++) {
      if (rows[j].closed && rows[j].ret != null) {
        closed.push(rows[j].ret); acc += rows[j].ret;
        if (rows[j].ret >= 0) wins++;
      }
    }
    if (closed.length) {
      var avg = acc / closed.length;
      sum = '<div class="tl-sum">확정 ' + closed.length + "건 · 승 " + wins +
        " · 평균 " + (avg >= 0 ? "+" : "") + fmtPct(avg) + "</div>";
    }
    return '<section class="trade-log"><div class="tl-head">매매 기록 <span>' +
      rows.length + '건</span></div><table class="tl"><thead><tr>' +
      "<th>종목</th><th>매수</th><th>매도</th><th>기간</th><th>손익</th><th></th>" +
      "</tr></thead><tbody>" + body + "</tbody></table>" + sum + "</section>";
  }
  // 차트 (라이브러리 0, 오프라인 동작). 데이터는 sanitizeChart 통과분만 온다.
  // candle = 봉 + 5·10일선 + BB(20,±2σ). line = 구버전 종가 라인 폴백.
  function chartHtml(ch, marks) {
    return ch.mode === "candle" ? candleHtml(ch, marks) : lineHtml(ch);
  }

  function lineHtml(ch) {
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

  function candleHtml(ch, marks) {
    var n = ch.c.length, W = 320, H = 120, TOP = 5, BOT = 5;
    // 스케일: 봉 고저 + 유효한 밴드/이평값까지 포함해 잘림 방지
    var min = Infinity, max = -Infinity, i, v;
    for (i = 0; i < n; i++) {
      if (ch.l[i] < min) min = ch.l[i];
      if (ch.h[i] > max) max = ch.h[i];
    }
    var pmin = min, pmax = max;  // 캡션용 실제 가격 저·고 (밴드 확장 전)
    var overlays = [ch.ma5, ch.ma10, ch.bb_mid, ch.bb_up, ch.bb_lo];
    for (var oi = 0; oi < overlays.length; oi++) {
      var a = overlays[oi];
      if (!a) continue;
      for (i = 0; i < n; i++) {
        v = a[i];
        if (v == null) continue;
        if (v < min) min = v;
        if (v > max) max = v;
      }
    }
    // 매수/매도 가격선이 범위 밖이면 스케일을 넓혀 항상 보이게 (D20b)
    if (marks) {
      if (marks.buy != null) { if (marks.buy < min) min = marks.buy; if (marks.buy > max) max = marks.buy; }
      if (marks.sell != null) { if (marks.sell < min) min = marks.sell; if (marks.sell > max) max = marks.sell; }
    }
    var span = (max - min) || 1;
    function Y(val) { return (TOP + (1 - (val - min) / span) * (H - TOP - BOT)).toFixed(1); }
    var xw = W / n, half = Math.max(0.8, xw * 0.32);

    // 지표 폴리라인: null(워밍업)은 건너뛰고 이어진 구간별로 그린다
    function poly(arr, cls) {
      if (!arr) return "";
      var segs = [], cur = [];
      for (var j = 0; j < n; j++) {
        if (arr[j] == null) { if (cur.length > 1) segs.push(cur); cur = []; continue; }
        cur.push(((j + 0.5) * xw).toFixed(1) + "," + Y(arr[j]));
      }
      if (cur.length > 1) segs.push(cur);
      var out = "";
      for (var s = 0; s < segs.length; s++) {
        out += '<polyline class="' + cls + '" fill="none" points="' + segs[s].join(" ") + '"/>';
      }
      return out;
    }

    // BB 영역: 상단·하단이 모두 있는 구간을 채움
    var band = "";
    if (ch.bb_up && ch.bb_lo) {
      var fwd = [], bwd = [];
      for (i = 0; i < n; i++) {
        if (ch.bb_up[i] == null || ch.bb_lo[i] == null) continue;
        var x = ((i + 0.5) * xw).toFixed(1);
        fwd.push(x + "," + Y(ch.bb_up[i]));
        bwd.unshift(x + "," + Y(ch.bb_lo[i]));
      }
      if (fwd.length > 1) band = '<polygon class="bb-area" points="' + fwd.join(" ") + " " + bwd.join(" ") + '"/>';
    }

    var candles = "";
    for (i = 0; i < n; i++) {
      var cx = ((i + 0.5) * xw).toFixed(1);
      var cls = ch.c[i] >= ch.o[i] ? "cu" : "cd";
      var yO = Y(ch.o[i]), yC = Y(ch.c[i]);
      var top = Math.min(yO, yC), hgt = Math.max(0.8, Math.abs(yO - yC));
      candles += '<line class="' + cls + '" x1="' + cx + '" y1="' + Y(ch.h[i]) + '" x2="' + cx + '" y2="' + Y(ch.l[i]) + '"/>' +
        '<rect class="' + cls + '" x="' + (((i + 0.5) * xw) - half).toFixed(1) + '" y="' + top +
        '" width="' + (half * 2).toFixed(1) + '" height="' + hgt.toFixed(1) + '"/>';
    }

    // 매수/매도 가격 기준선 + 라벨 (배지와 동일 색 체계). 봉 날짜별 X 위치는
    // 서버가 봉 날짜 배열을 싣지 않아 불가 — 가격 기준 가로선으로 표시(정직한 근사).
    var mk = "";
    function mline(p, cls, label) {
      if (p == null) return "";
      var y = parseFloat(Y(p));
      var ty = y < 12 ? y + 9 : y - 3;   // 상단 잘림 방지
      return '<line class="' + cls + '" x1="0" y1="' + y + '" x2="' + W + '" y2="' + y + '"/>' +
        '<text class="' + cls + '-t" x="3" y="' + ty.toFixed(1) + '">' +
        label + " " + p.toLocaleString() + "</text>";
    }
    if (marks) {
      mk += mline(marks.buy, "mk-buy", "매수");
      mk += mline(marks.sell, "mk-sell", "매도");
    }

    var chg = ch.c[n - 1] / ch.c[0] - 1;
    var cc = chg >= 0 ? "pos" : "neg";
    return '<div class="chart-wrap">' +
      '<svg class="spark candle" viewBox="0 0 ' + W + " " + H + '" aria-hidden="true">' +
      band + poly(ch.bb_up, "bb-line") + poly(ch.bb_lo, "bb-line") + poly(ch.bb_mid, "bb-mid") +
      candles + poly(ch.ma5, "ma5") + poly(ch.ma10, "ma10") + mk + "</svg>" +
      '<div class="chart-cap"><span class="legend">' +
      '<i class="lg-ma5"></i>5일 <i class="lg-ma10"></i>10일 <i class="lg-bb"></i>BB(20,2σ)</span>' +
      '<span>저 ' + fmtNum(pmin, 2) + " · 고 " + fmtNum(pmax, 2) + "</span></div>" +
      '<div class="chart-cap"><span>' + esc(ch.start || "") + " ~ " + esc(ch.end || "") + " · " + n + '봉</span>' +
      '<span>기간 <b class="' + cc + '">' + (chg >= 0 ? "+" : "") + fmtPct(chg) + "</b></span></div></div>";
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
    ["s1", "s2", "both", "watch"].forEach(function (t) {
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
      // ☆/삭제 버튼이 먼저 — 카드 펼침 토글로 흘러가지 않게 여기서 끝낸다
      var star = e.target.closest(".star");
      if (star) { toggleWatch(star.getAttribute("data-tk")); return; }
      var del = e.target.closest(".watch-del");
      if (del) { removeWatch(del.getAttribute("data-tk")); return; }
      if (e.target.closest(".trade-ok")) { confirmTradeInput(); return; }
      if (e.target.closest(".trade-cancel")) { cancelTradeInput(); return; }
      var tld = e.target.closest(".tl-del");
      if (tld) {
        deleteTrade(tld.getAttribute("data-tk"), parseInt(tld.getAttribute("data-i"), 10));
        return;
      }
      var clr = e.target.closest(".trade-clear");
      if (clr) { deleteTrade(clr.getAttribute("data-tk"), -1); return; }
      var buy = e.target.closest(".trade-buy");
      if (buy) { openTradeInput(buy.getAttribute("data-tk"), "buy"); return; }
      var sell = e.target.closest(".trade-sell");
      if (sell) { openTradeInput(sell.getAttribute("data-tk"), "sell"); return; }
      if (e.target.closest(".trade-form")) return;  // 입력칸 터치가 카드 접힘으로 새지 않게
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
    loadWatch();
    loadCachedThenRevalidate();
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("./sw.js").catch(function () {});
    }
  }
  document.addEventListener("DOMContentLoaded", init);
})();
