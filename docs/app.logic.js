/* app.logic.js — DOM 없는 순수 로직 (전역 AppLogic).
 * 불신(§10): 깨진/구버전/빈 results.json 이 와도 sanitize 가 안전한 형태로 정규화.
 * regime.ok 는 3상(true/false/null)을 보존한다 — null=판정불가는 false 와 다른 상태.
 * app.js(DOM)와 테스트(py_mini_racer)가 이 파일을 공유한다.
 */
var AppLogic = (function () {
  "use strict";

  function num(v) { return (typeof v === "number" && isFinite(v)) ? v : null; }
  function str(v) {
    if (typeof v === "string") return v;
    if (typeof v === "number" && isFinite(v)) return String(v);
    return null;
  }
  function bool(v) { return v === true; }
  function tri(v) { return v === true ? true : (v === false ? false : null); }

  function esc(s) {
    if (s == null) return "";
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function fmtMoney(v) {
    v = num(v);
    if (v == null) return "—";
    if (v >= 1e9) return "$" + (v / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
    if (v >= 1e6) return "$" + Math.round(v / 1e6) + "M";
    return "$" + Math.round(v).toLocaleString();
  }

  function fmtNum(v, digits) {
    v = num(v);
    if (v == null) return "—";
    return v.toFixed(digits == null ? 2 : digits);
  }

  function fmtPct(v, digits) {
    v = num(v);
    if (v == null) return "—";
    return (v * 100).toFixed(digits == null ? 1 : digits) + "%";
  }

  function sanitizeS1(raw) {
    if (!raw || typeof raw !== "object") return null;
    return {
      gc_date: str(raw.gc_date),
      slow_k: num(raw.slow_k), slow_d: num(raw.slow_d),
      macd: num(raw.macd), signal: num(raw.signal), hist: num(raw.hist),
      avg_dollar_volume: num(raw.avg_dollar_volume),
      sma200: num(raw.sma200), vol_ratio: num(raw.vol_ratio), adx: num(raw.adx)
    };
  }

  function sanitizeS2(raw) {
    if (!raw || typeof raw !== "object") return null;
    return {
      rs_3m: num(raw.rs_3m), rs_6m: num(raw.rs_6m),
      sma50: num(raw.sma50), sma200: num(raw.sma200),
      drawdown: num(raw.drawdown), pullback_low_pct: num(raw.pullback_low_pct),
      trigger: str(raw.trigger), vol_ratio: num(raw.vol_ratio)
    };
  }

  // 차트는 부가 정보: 오염되면 차트(또는 해당 오버레이)만 버리고 카드는 산다.
  // 길이 상한 260 — 변조로 거대 배열이 와도 렌더 비용을 제한.
  function priceArr(raw, n) {          // 가격 배열: 길이 일치 + 전부 양수
    if (!Array.isArray(raw) || raw.length !== n) return null;
    var out = [];
    for (var i = 0; i < n; i++) {
      var v = num(raw[i]);
      if (v == null || v <= 0) return null;
      out.push(v);
    }
    return out;
  }
  function lineArr(raw, n) {           // 지표 배열: null(워밍업) 허용, 숫자면 유한값만
    if (!Array.isArray(raw) || raw.length !== n) return null;
    var out = [];
    for (var i = 0; i < n; i++) {
      if (raw[i] == null) { out.push(null); continue; }
      var v = num(raw[i]);
      if (v == null) return null;
      out.push(v);
    }
    return out;
  }
  function sanitizeChart(raw) {
    if (!raw || typeof raw !== "object") return null;
    var start = str(raw.start), end = str(raw.end);
    // 봉차트: o/h/l/c 4배열이 모두 유효할 때만. 오버레이는 각자 유효할 때만 살린다.
    if (Array.isArray(raw.c) && raw.c.length >= 2 && raw.c.length <= 260) {
      var n = raw.c.length;
      var o = priceArr(raw.o, n), h = priceArr(raw.h, n), l = priceArr(raw.l, n), c = priceArr(raw.c, n);
      if (o && h && l && c) {
        return {
          mode: "candle", o: o, h: h, l: l, c: c,
          ma5: lineArr(raw.ma5, n), ma10: lineArr(raw.ma10, n),
          bb_mid: lineArr(raw.bb_mid, n), bb_up: lineArr(raw.bb_up, n), bb_lo: lineArr(raw.bb_lo, n),
          start: start, end: end
        };
      }
    }
    // 폴백: 종가 라인 (구버전 results.json / 봉 데이터 오염 시)
    if (!Array.isArray(raw.closes) || raw.closes.length > 260) return null;
    var closes = [];
    for (var i2 = 0; i2 < raw.closes.length; i2++) {
      var v2 = num(raw.closes[i2]);
      if (v2 == null || v2 <= 0) return null;
      closes.push(v2);
    }
    if (closes.length < 2) return null;
    return { mode: "line", closes: closes, start: start, end: end };
  }

  // ---- 관심종목/시세 (D19) --------------------------------------------------
  // quotes: 전 유니버스 경량 시세 — 저장 종목이 스크리닝에서 빠진 날에도 추적.
  // 구버전 results.json 엔 없음 → 빈 객체로 정규화(앱은 "시세 없음" 표시).
  function sanitizeQuotes(raw) {
    var out = {};
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return out;
    var keys = Object.keys(raw), n = 0;
    for (var i = 0; i < keys.length && n < 500; i++) {   // 개수 상한: 변조 방어
      var q = raw[keys[i]];
      if (!q || typeof q !== "object") continue;
      var price = num(q.price);
      if (price == null || price <= 0) continue;
      out[keys[i]] = { price: price, chg: num(q.chg) };
      n++;
    }
    return out;
  }

  // 관심 목록은 폰 localStorage 소유 — 여기도 불신(변조/구버전/중복) 정규화.
  // 상한 200: 변조로 거대 배열이 와도 렌더·저장 비용을 제한.
  function sanitizeWatch(raw) {
    if (!Array.isArray(raw)) return [];
    var out = [], seen = {};
    for (var i = 0; i < raw.length && out.length < 200; i++) {
      var e = raw[i];
      if (!e || typeof e !== "object") continue;
      var tk = str(e.ticker);
      if (!tk || seen[tk]) continue;                     // 중복은 첫 항목 유지
      seen[tk] = true;
      var sp = num(e.saved_price);
      // D20 가상 매매 표식: 매수 없는 매도는 모순 → 매도만 버린다(일관성)
      var bp = num(e.buy_price), slp = num(e.sell_price);
      bp = (bp != null && bp > 0) ? bp : null;
      slp = (bp != null && slp != null && slp > 0) ? slp : null;
      out.push({
        ticker: tk,
        name: str(e.name) || tk,
        sector_kr: str(e.sector_kr) || "미분류",
        saved_at: str(e.saved_at),                       // 저장한 날짜 (기기 기준)
        saved_as_of: str(e.saved_as_of),                 // 저장 당시 데이터 기준일
        saved_price: (sp != null && sp > 0) ? sp : null, // 저장가 (수익률 기준)
        buy_price: bp, buy_at: bp != null ? str(e.buy_at) : null,
        sell_price: slp, sell_at: slp != null ? str(e.sell_at) : null
      });
    }
    return out;
  }

  // D20 가상 매매 손익. 매수 없으면 null.
  // 매도됨 → {closed:true, ret:매도/매수-1} (확정 — 현재가와 무관).
  // 보유중 → {closed:false, ret:현재가/매수-1 | null(시세 없음)}.
  function tradeReturn(entry, curPrice) {
    if (!entry) return null;
    var bp = num(entry.buy_price), sp = num(entry.sell_price), cp = num(curPrice);
    if (bp == null || bp <= 0) return null;
    if (sp != null && sp > 0) return { closed: true, ret: sp / bp - 1 };
    if (cp == null || cp <= 0) return { closed: false, ret: null };
    return { closed: false, ret: cp / bp - 1 };
  }

  // D20b: 사용자 입력 가격 파서 — 쉼표 허용, 양수·유한값만, 센트 반올림.
  function parsePrice(v) {
    if (typeof v === "number") v = String(v);
    if (typeof v !== "string") return null;
    var s = v.replace(/,/g, "").trim();
    if (!s) return null;
    var n = Number(s);
    if (!isFinite(n) || n <= 0) return null;
    return Math.round(n * 100) / 100;
  }

  function watchReturn(entry, curPrice) {
    var sp = entry ? num(entry.saved_price) : null, cp = num(curPrice);
    if (sp == null || sp <= 0 || cp == null || cp <= 0) return null;
    return cp / sp - 1;
  }

  // prices: {ticker: 현재가|null}. key: saved_at(기본, 최신순) | ret | name
  function sortWatch(entries, prices, key) {
    var out = entries.slice();
    var pr = prices || {};
    out.sort(function (a, b) {
      if (key === "name") return a.ticker < b.ticker ? -1 : (a.ticker > b.ticker ? 1 : 0);
      if (key === "ret") {
        var ra = watchReturn(a, pr[a.ticker]), rb = watchReturn(b, pr[b.ticker]);
        if (ra == null && rb == null) return a.ticker < b.ticker ? -1 : 1;
        if (ra == null) return 1;   // 판정불가는 뒤로
        if (rb == null) return -1;
        return rb - ra;
      }
      var sa = a.saved_at || "", sb = b.saved_at || "";
      if (sa !== sb) return sa < sb ? 1 : -1;            // 최신 저장이 위로
      return a.ticker < b.ticker ? -1 : 1;
    });
    return out;
  }

  function sanitizeItem(raw) {
    if (!raw || typeof raw !== "object") return null;
    var ticker = str(raw.ticker);
    if (!ticker) return null;
    return {
      ticker: ticker,
      name: str(raw.name) || ticker,
      sector: str(raw.sector) || "",
      sector_kr: str(raw.sector_kr) || "미분류",
      price: num(raw.price),
      pass_s1: bool(raw.pass_s1),
      pass_s2: bool(raw.pass_s2),
      s1: sanitizeS1(raw.s1),
      s2: sanitizeS2(raw.s2),
      chart: sanitizeChart(raw.chart)
    };
  }

  function sanitize(raw) {
    if (!raw || typeof raw !== "object") return null;
    var items = [];
    if (Array.isArray(raw.items)) {
      for (var i = 0; i < raw.items.length; i++) {
        var it = sanitizeItem(raw.items[i]);
        if (it) items.push(it);
      }
    }
    var sectors = [];
    if (Array.isArray(raw.sectors)) {
      for (var j = 0; j < raw.sectors.length; j++) {
        var s = raw.sectors[j];
        if (!s || typeof s !== "object") continue;
        var key = str(s.key);
        if (!key) continue;
        sectors.push({
          key: key,
          color: str(s.color) || "#9CA3AF",
          count_s1: num(s.count_s1) || 0,
          count_s2: num(s.count_s2) || 0
        });
      }
    }
    var regime = (raw.regime && typeof raw.regime === "object") ? raw.regime : {};
    var counts = (raw.counts && typeof raw.counts === "object") ? raw.counts : {};
    // counts 누락/불일치 방어: items 에서 재계산 가능해야 앱이 일관 표시
    var n1 = 0, n2 = 0, nb = 0;
    for (var k = 0; k < items.length; k++) {
      if (items[k].pass_s1) n1++;
      if (items[k].pass_s2) n2++;
      if (items[k].pass_s1 && items[k].pass_s2) nb++;
    }
    var cs = (raw.config_summary && typeof raw.config_summary === "object")
      ? raw.config_summary : {};
    return {
      as_of: str(raw.as_of),
      generated_at: str(raw.generated_at),
      stale: bool(raw.stale),
      universe_count: num(raw.universe_count),
      regime: {
        enabled: regime.enabled !== false,
        ok: tri(regime.ok),
        qqq_close: num(regime.qqq_close),
        qqq_sma200: num(regime.qqq_sma200)
      },
      counts: {
        s1: num(counts.s1) != null ? num(counts.s1) : n1,
        s2: num(counts.s2) != null ? num(counts.s2) : n2,
        both: num(counts.both) != null ? num(counts.both) : nb
      },
      config_summary: { s1: str(cs.s1), s2: str(cs.s2) },
      errors_count: num(raw.errors_count) || 0,
      sectors: sectors,
      quotes: sanitizeQuotes(raw.quotes),
      items: items
    };
  }

  // ---- 탭/필터/정렬/그룹 ----------------------------------------------------
  function itemsForTab(items, tab) {
    var out = [];
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      if (tab === "s1" && it.pass_s1) out.push(it);
      else if (tab === "s2" && it.pass_s2) out.push(it);
      else if (tab === "both" && it.pass_s1 && it.pass_s2) out.push(it);
    }
    return out;
  }

  function sortValue(it, key) {
    if (key === "dollar_volume") return it.s1 ? it.s1.avg_dollar_volume : null;
    if (key === "slow_k") return it.s1 ? it.s1.slow_k : null;
    if (key === "rs_3m") return it.s2 ? it.s2.rs_3m : null;
    if (key === "drawdown") return it.s2 ? it.s2.drawdown : null;
    return null;
  }

  function filterSort(items, activeSectors, sortKey) {
    var act = activeSectors || [];
    var out = [];
    for (var i = 0; i < items.length; i++) {
      if (act.length === 0 || act.indexOf(items[i].sector_kr) !== -1) out.push(items[i]);
    }
    out.sort(function (a, b) {
      if (sortKey === "name") return a.ticker < b.ticker ? -1 : (a.ticker > b.ticker ? 1 : 0);
      var va = sortValue(a, sortKey), vb = sortValue(b, sortKey);
      if (va == null && vb == null) return a.ticker < b.ticker ? -1 : 1;
      if (va == null) return 1;   // null 은 뒤로
      if (vb == null) return -1;
      return vb - va;             // 값 내림차순 (drawdown 도 0 에 가까운 순)
    });
    return out;
  }

  function groupBySector(items, sectorOrder) {
    var order = sectorOrder || [];
    var map = {}, keys = [];
    for (var i = 0; i < items.length; i++) {
      var k = items[i].sector_kr;
      if (!map[k]) { map[k] = []; keys.push(k); }
      map[k].push(items[i]);
    }
    keys.sort(function (a, b) {
      var ia = order.indexOf(a), ib = order.indexOf(b);
      if (ia === -1) ia = 999;
      if (ib === -1) ib = 999;
      return ia - ib;
    });
    var groups = [];
    for (var j = 0; j < keys.length; j++) {
      groups.push({ key: keys[j], items: map[keys[j]] });
    }
    return groups;
  }

  return {
    sanitize: sanitize,
    sanitizeQuotes: sanitizeQuotes,
    sanitizeWatch: sanitizeWatch,
    watchReturn: watchReturn,
    tradeReturn: tradeReturn,
    parsePrice: parsePrice,
    sortWatch: sortWatch,
    itemsForTab: itemsForTab,
    filterSort: filterSort,
    groupBySector: groupBySector,
    fmtMoney: fmtMoney,
    fmtNum: fmtNum,
    fmtPct: fmtPct,
    esc: esc
  };
})();

if (typeof module !== "undefined" && module.exports) module.exports = AppLogic;
