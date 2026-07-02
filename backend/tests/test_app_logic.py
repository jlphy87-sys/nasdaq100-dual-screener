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
