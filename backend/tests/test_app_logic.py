"""
test_app_logic.py — 프런트 순수 로직(app.logic.js)을 실제 JS 엔진(py_mini_racer)으로 검증.
Node 없이도 진짜 런타임 검증(명세 §11 스키마·앱).

mock 4종(정상/빈/깨진/regime-false)을 sanitize 에 주입해 크래시 없이 안전한
형태가 나오는지 + 탭 분류/정렬/그룹/포맷 정확성.
"""

import json
import os

import pytest

pytest.importorskip("py_mini_racer")

DOCS = os.path.join(os.path.dirname(__file__), "..", "..", "docs")
MOCKS = os.path.join(DOCS, "data", "mocks")


def _ctx():
    from py_mini_racer import MiniRacer

    c = MiniRacer()
    with open(os.path.join(DOCS, "app.logic.js"), encoding="utf-8") as f:
        c.eval(f.read())
    return c


def _mock(name):
    with open(os.path.join(MOCKS, f"mock_{name}.json"), encoding="utf-8") as f:
        return json.load(f)


def _sanitize(c, raw):
    return json.loads(c.eval("JSON.stringify(AppLogic.sanitize(%s))" % json.dumps(raw)))


# ---- mock 4종 ---------------------------------------------------------------
def test_sanitize_good_mock_roundtrip():
    c = _ctx()
    good = _mock("good")
    out = _sanitize(c, good)
    assert out["as_of"] == "2026-07-01"
    assert out["regime"]["ok"] is True
    assert len(out["items"]) == 6
    assert out["counts"] == {"s1": 4, "s2": 3, "both": 1}
    # s1 블록 없는 종목(TSLA)은 s1=null 로 정규화
    tsla = next(i for i in out["items"] if i["ticker"] == "TSLA")
    assert tsla["s1"] is None and tsla["s2"]["rs_3m"] == 1.35


def test_sanitize_empty_mock():
    c = _ctx()
    out = _sanitize(c, _mock("empty"))
    assert out["items"] == [] and out["counts"] == {"s1": 0, "s2": 0, "both": 0}


def test_sanitize_broken_mock_no_crash():
    c = _ctx()
    out = _sanitize(c, _mock("broken"))
    assert out["items"] == [] and out["sectors"] == []
    assert out["as_of"] == "20260701"      # 숫자 → 문자열 정규화
    assert out["stale"] is False           # "yes" → False (엄격 bool)
    assert out["regime"]["ok"] is None     # 깨진 regime → 판정불가
    assert out["counts"] == {"s1": 0, "s2": 0, "both": 0}  # 배열 counts → 재계산


def test_sanitize_regime_false_mock_preserves_tristate():
    c = _ctx()
    out = _sanitize(c, _mock("regime_false"))
    assert out["regime"]["ok"] is False    # false 는 null 과 다른 상태(3상 보존)
    assert out["counts"]["s2"] == 0


def test_sanitize_null_and_missing_fields():
    c = _ctx()
    assert json.loads(c.eval("JSON.stringify(AppLogic.sanitize(null))")) is None
    out = _sanitize(c, {"items": [{"ticker": "AAPL"}]})
    it = out["items"][0]
    assert it["price"] is None and it["pass_s1"] is False and it["sector_kr"] == "미분류"


# ---- 탭 분류 -----------------------------------------------------------------
def test_items_for_tab_split():
    c = _ctx()
    items = _sanitize(c, _mock("good"))["items"]
    js = "var IT=%s;" % json.dumps(items)
    c.eval(js)
    n1 = c.eval("AppLogic.itemsForTab(IT,'s1').length")
    n2 = c.eval("AppLogic.itemsForTab(IT,'s2').length")
    nb = c.eval("AppLogic.itemsForTab(IT,'both').length")
    assert (n1, n2, nb) == (4, 3, 1)
    assert c.eval("AppLogic.itemsForTab(IT,'both')[0].ticker") == "NVDA"


# ---- 정렬/그룹/포맷 -----------------------------------------------------------
def test_filter_sort_rs3m_and_nulls_last():
    c = _ctx()
    items = [
        {"ticker": "A", "sector_kr": "금융", "s2": {"rs_3m": 1.1}},
        {"ticker": "B", "sector_kr": "금융", "s2": {"rs_3m": 1.5}},
        {"ticker": "C", "sector_kr": "금융", "s2": None},
    ]
    js = "JSON.stringify(AppLogic.filterSort(%s, [], 'rs_3m'))" % json.dumps(items)
    out = json.loads(c.eval(js))
    assert [x["ticker"] for x in out] == ["B", "A", "C"]  # null 은 뒤로


def test_filter_sort_sector_filter_and_group_order():
    c = _ctx()
    items = [
        {"ticker": "A", "sector_kr": "금융", "s1": {"avg_dollar_volume": 100}},
        {"ticker": "B", "sector_kr": "IT/기술", "s1": {"avg_dollar_volume": 300}},
        {"ticker": "C", "sector_kr": "금융", "s1": {"avg_dollar_volume": 200}},
    ]
    js = "JSON.stringify(AppLogic.filterSort(%s, ['금융'], 'dollar_volume'))" % json.dumps(items)
    fin = json.loads(c.eval(js))
    assert [x["ticker"] for x in fin] == ["C", "A"]

    js2 = "JSON.stringify(AppLogic.groupBySector(%s, ['IT/기술','금융']))" % json.dumps(items)
    groups = json.loads(c.eval(js2))
    assert [g["key"] for g in groups] == ["IT/기술", "금융"]


def test_formats():
    c = _ctx()
    assert c.eval("AppLogic.fmtMoney(32100000000)") == "$32.1B"
    assert c.eval("AppLogic.fmtMoney(950000000)") == "$950M"
    assert c.eval("AppLogic.fmtMoney(null)") == "—"
    assert c.eval("AppLogic.fmtPct(-0.062)") == "-6.2%"
    assert c.eval("AppLogic.fmtNum('bad')") == "—"


# ---- 관심종목/quotes (D19 — 경계 4종: 정상/누락/변조/상한) ----------------------
def test_sanitize_quotes_normal_missing_tampered():
    c = _ctx()
    good = _mock("good")
    good["quotes"] = {"AAPL": {"price": 212.44, "chg": 0.012},
                      "BAD1": {"price": -5, "chg": 0},      # 음수 가격 → 항목 제외
                      "BAD2": "악성",                        # 비객체 → 제외
                      "NOCHG": {"price": 10.0, "chg": "x"}}  # chg 변조 → null 강등
    out = _sanitize(c, good)
    assert out["quotes"]["AAPL"] == {"price": 212.44, "chg": 0.012}
    assert "BAD1" not in out["quotes"] and "BAD2" not in out["quotes"]
    assert out["quotes"]["NOCHG"]["chg"] is None
    del good["quotes"]  # 누락(구버전 results.json) → 빈 객체
    assert _sanitize(c, good)["quotes"] == {}


def test_sanitize_watch_normal_dedupe_cap_tampered():
    c = _ctx()

    def run(raw):
        return json.loads(c.eval("JSON.stringify(AppLogic.sanitizeWatch(%s))" % json.dumps(raw)))

    ok = run([{"ticker": "AAPL", "name": "Apple", "sector_kr": "IT/기술",
               "saved_at": "2026-07-04", "saved_as_of": "2026-07-02", "saved_price": 212.44},
              {"ticker": "AAPL", "saved_price": 1},     # 중복 → 첫 항목 유지
              {"ticker": "MSFT", "saved_price": -3},    # 음수 저장가 → null 강등
              {"no_ticker": True}, "악성", None])        # 티커 없음/비객체 → 제외
    assert [e["ticker"] for e in ok] == ["AAPL", "MSFT"]
    assert ok[0]["saved_at"] == "2026-07-04" and ok[0]["saved_price"] == 212.44
    assert ok[1]["saved_price"] is None and ok[1]["name"] == "MSFT"
    assert run("not-an-array") == [] and run(None) == []
    assert len(run([{"ticker": "T%d" % i} for i in range(300)])) == 200  # 상한


def test_watch_return_and_sort():
    c = _ctx()
    entries = [{"ticker": "A", "saved_at": "2026-07-01", "saved_price": 100.0},
               {"ticker": "B", "saved_at": "2026-07-03", "saved_price": 200.0},
               {"ticker": "C", "saved_at": None, "saved_price": None}]
    c.eval("var EN=%s; var PR={A:110.0,B:190.0,C:50.0};" % json.dumps(entries))
    assert abs(c.eval("AppLogic.watchReturn(EN[0], 110.0)") - 0.10) < 1e-9
    assert c.eval("AppLogic.watchReturn(EN[2], 50.0)") is None  # 저장가 없음 → 판정불가
    by_date = json.loads(c.eval(
        "JSON.stringify(AppLogic.sortWatch(EN, PR, 'saved_at').map(function(e){return e.ticker;}))"))
    assert by_date == ["B", "A", "C"]  # 최신 저장 먼저, 날짜 없음은 뒤로
    by_ret = json.loads(c.eval(
        "JSON.stringify(AppLogic.sortWatch(EN, PR, 'ret').map(function(e){return e.ticker;}))"))
    assert by_ret == ["A", "B", "C"]   # +10% > -5% > 판정불가


# ---- 가상 매매 표식 (D20 — 정상/누락/변조/모순) --------------------------------
def test_sanitize_watch_trade_fields_and_orphan_sell():
    c = _ctx()

    def run(raw):
        return json.loads(c.eval("JSON.stringify(AppLogic.sanitizeWatch(%s))" % json.dumps(raw)))

    # 정상: 매수·매도 왕복 보존
    ok = run([{"ticker": "A", "buy_price": 50.0, "buy_at": "2026-06-21",
               "sell_price": 55.0, "sell_at": "2026-07-01"}])[0]
    assert ok["buy_price"] == 50.0 and ok["sell_price"] == 55.0
    assert ok["buy_at"] == "2026-06-21" and ok["sell_at"] == "2026-07-01"
    # 누락: 매매 기록 없음 → null 정규화
    none = run([{"ticker": "B"}])[0]
    assert none["buy_price"] is None and none["sell_price"] is None
    # 모순: 매수 없는 매도 → 매도만 버림
    orphan = run([{"ticker": "C", "sell_price": 55.0, "sell_at": "2026-07-01"}])[0]
    assert orphan["sell_price"] is None and orphan["sell_at"] is None
    # 변조: 비수치/음수 매수가 → 매수·매도 모두 null (매도는 매수에 종속)
    bad = run([{"ticker": "D", "buy_price": "악성", "sell_price": 55.0}])[0]
    assert bad["buy_price"] is None and bad["sell_price"] is None


def test_parse_price_boundaries():
    c = _ctx()
    assert c.eval("AppLogic.parsePrice('123.45')") == 123.45
    assert c.eval("AppLogic.parsePrice('1,234.5')") == 1234.5   # 쉼표 허용
    assert c.eval("AppLogic.parsePrice(50)") == 50              # 숫자 매핑
    assert c.eval("AppLogic.parsePrice('50.129')") == 50.13     # 센트 반올림
    for bad in ("''", "null", "'abc'", "'-5'", "'0'", "'1e999'"):
        assert c.eval("AppLogic.parsePrice(%s)" % bad) is None  # 변조/누락 거부


def test_trade_return_states():
    c = _ctx()

    def tr(entry, cur):
        return json.loads(c.eval(
            "JSON.stringify(AppLogic.tradeReturn(%s, %s))" % (json.dumps(entry), json.dumps(cur))))

    assert tr({"ticker": "A"}, 55.0) is None                       # 매수 없음
    open_ = tr({"buy_price": 50.0}, 55.0)                          # 보유중 평가
    assert open_["closed"] is False and abs(open_["ret"] - 0.10) < 1e-9
    nocur = tr({"buy_price": 50.0}, None)                          # 시세 없음
    assert nocur["closed"] is False and nocur["ret"] is None
    closed = tr({"buy_price": 50.0, "sell_price": 45.0}, 999.0)    # 확정 — 현재가 무시
    assert closed["closed"] is True and abs(closed["ret"] - (-0.10)) < 1e-9


# ---- 매매 이력·기록 표 (D20c — 정상/불완전/변조/상한) ---------------------------
def test_sanitize_watch_trades_history():
    c = _ctx()

    def run(raw):
        return json.loads(c.eval("JSON.stringify(AppLogic.sanitizeWatch(%s))" % json.dumps(raw)))

    ok = run([{"ticker": "A", "trades": [
        {"buy_price": 100.0, "buy_at": "2026-06-01", "sell_price": 110.0, "sell_at": "2026-06-10"},
        {"buy_price": 100.0},                       # 불완전(매도 없음) → 제외
        {"buy_price": "악성", "sell_price": 110.0},  # 변조 → 제외
        "쓰레기", None]}])[0]
    assert len(ok["trades"]) == 1
    assert ok["trades"][0] == {"buy_price": 100.0, "buy_at": "2026-06-01",
                               "sell_price": 110.0, "sell_at": "2026-06-10"}
    assert run([{"ticker": "B"}])[0]["trades"] == []             # 누락 → 빈 배열
    many = [{"buy_price": 1.0, "sell_price": 2.0}] * 60
    assert len(run([{"ticker": "C", "trades": many}])[0]["trades"]) == 50  # 상한


def test_trade_rows_merge_history_and_current():
    c = _ctx()
    entries = [
        {"ticker": "A", "saved_price": 1,
         "trades": [{"buy_price": 100.0, "buy_at": "2026-06-01",
                     "sell_price": 90.0, "sell_at": "2026-06-10"}],
         "buy_price": 80.0, "buy_at": "2026-07-01"},            # 보유중
        {"ticker": "B", "saved_price": 1, "buy_price": 200.0, "buy_at": "2026-06-15",
         "sell_price": 220.0, "sell_at": "2026-06-20"},          # 확정(현재 사이클)
    ]
    c.eval("var EN = AppLogic.sanitizeWatch(%s); var PR={A:88.0,B:999.0};" % json.dumps(entries))
    rows = json.loads(c.eval("JSON.stringify(AppLogic.tradeRows(EN, PR))"))
    assert len(rows) == 3
    assert [r["ticker"] for r in rows] == ["A", "B", "A"]        # 최근 활동순
    open_a = rows[0]
    assert open_a["i"] == -1 and open_a["closed"] is False
    assert abs(open_a["ret"] - 0.10) < 1e-9                      # 88/80 평가
    assert rows[1]["closed"] is True and abs(rows[1]["ret"] - 0.10) < 1e-9  # 220/200
    assert rows[2]["i"] == 0 and abs(rows[2]["ret"] - (-0.10)) < 1e-9       # 90/100


# ---- chart 필드 (경계 4종: 정상/누락/변조/과대) --------------------------------
def test_sanitize_chart_normal_and_missing():
    c = _ctx()
    good = _mock("good")
    good["items"][0]["chart"] = {"closes": [100.0, 101.5, 99.2], "start": "2026-04-01", "end": "2026-07-01"}
    out = _sanitize(c, good)
    it0 = next(i for i in out["items"] if i["ticker"] == good["items"][0]["ticker"])
    assert it0["chart"]["closes"] == [100.0, 101.5, 99.2]
    assert it0["chart"]["start"] == "2026-04-01"
    # chart 없는 나머지 종목은 null 정규화 (구버전 results.json 호환)
    others = [i for i in out["items"] if i["ticker"] != it0["ticker"]]
    assert all(i["chart"] is None for i in others)


def test_sanitize_chart_tampered_dropped_card_survives():
    c = _ctx()
    good = _mock("good")
    tk = good["items"][0]["ticker"]
    for bad in (
        {"closes": [100, "악성", 99]},          # 비수치 혼입
        {"closes": [100, -5, 99]},              # 0 이하
        {"closes": [100]},                      # 2개 미만
        {"closes": [1.0] * 261},                # 길이 상한 초과
        {"closes": "not-an-array"},
        "not-an-object",
    ):
        good["items"][0]["chart"] = bad
        out = _sanitize(c, good)
        it0 = next(i for i in out["items"] if i["ticker"] == tk)
        assert it0["chart"] is None            # 차트만 버림
        assert it0["ticker"] == tk             # 카드는 산다


def test_sanitize_chart_candle_mode_and_overlay_isolation():
    c = _ctx()
    good = _mock("good")
    tk = good["items"][0]["ticker"]
    base = {
        "closes": [100, 102, 101, 103],
        "o": [99, 101, 102, 101], "h": [101, 103, 103, 104],
        "l": [98, 100, 100, 100], "c": [100, 102, 101, 103],
        "ma5": [None, None, 101.0, 102.0], "ma10": [None, None, None, 101.5],
        "bb_mid": [None, 101.0, 101.5, 102.0],
        "bb_up": [None, 103.0, 104.0, 105.0], "bb_lo": [None, 99.0, 99.5, 99.0],
        "start": "2026-06-27", "end": "2026-07-02",
    }
    good["items"][0]["chart"] = base
    out = _sanitize(c, good)
    ch = next(i for i in out["items"] if i["ticker"] == tk)["chart"]
    assert ch["mode"] == "candle"
    assert ch["h"] == [101, 103, 103, 104] and ch["ma5"][2] == 101.0

    # 오버레이만 오염 → 봉차트는 살고 해당 오버레이만 null
    bad = json.loads(json.dumps(base))
    bad["ma5"] = [1, "악성", 3, 4]
    good["items"][0]["chart"] = bad
    ch2 = next(i for i in _sanitize(c, good)["items"] if i["ticker"] == tk)["chart"]
    assert ch2["mode"] == "candle" and ch2["ma5"] is None and ch2["ma10"] is not None

    # 봉 데이터 오염(길이 불일치) → closes 라인 폴백
    bad2 = json.loads(json.dumps(base))
    bad2["h"] = [101, 103]
    good["items"][0]["chart"] = bad2
    ch3 = next(i for i in _sanitize(c, good)["items"] if i["ticker"] == tk)["chart"]
    assert ch3["mode"] == "line" and ch3["closes"] == [100, 102, 101, 103]
