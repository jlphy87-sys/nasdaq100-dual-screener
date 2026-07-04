"""
test_app_render.py — 실제 app.js 렌더 파이프라인을 DOM 스텁 위에서 마운트해 검증.
(명세 §11: mock 4종 주입 → 크래시 없이 올바른 상태 렌더 + 탭 전환.)
"""

import json
import os

import pytest

pytest.importorskip("py_mini_racer")
from py_mini_racer import MiniRacer  # noqa: E402

DOCS = os.path.join(os.path.dirname(__file__), "..", "..", "docs")
MOCKS = os.path.join(DOCS, "data", "mocks")

# 최소 DOM/브라우저 스텁 + 동기 thenable fetch (마이크로태스크 없이 즉시 콜백)
DOM_STUB = r"""
var __els = {};
function makeEl(id){
  var cls = {};
  var el = {
    id:id, _html:"", _text:"", _handlers:{}, style:{}, value:"",
    classList:{
      toggle:function(c,on){ cls[c] = (on===undefined)? !cls[c] : !!on; },
      add:function(c){ cls[c]=true; }, remove:function(c){ cls[c]=false; },
      contains:function(c){ return !!cls[c]; }
    },
    addEventListener:function(ev,cb){ el._handlers[ev]=cb; },
    getAttribute:function(){ return null; },
    closest:function(){ return null; }, querySelector:function(){ return null; }
  };
  Object.defineProperty(el,"innerHTML",{get:function(){return el._html;},set:function(v){el._html=v;}});
  Object.defineProperty(el,"textContent",{get:function(){return el._text;},set:function(v){el._text=v;}});
  return el;
}
var document = {
  _ready:null,
  addEventListener:function(ev,cb){ if(ev==="DOMContentLoaded") document._ready=cb; },
  getElementById:function(id){ if(!__els[id]) __els[id]=makeEl(id); return __els[id]; }
};
var window = { addEventListener:function(){}, scrollY:0 };
var navigator = {};
var __ls = {};
var localStorage = {
  getItem:function(k){ return (k in __ls)? __ls[k] : null; },
  setItem:function(k,v){ __ls[k]=String(v); }
};
function setTimeout(){ return 0; }
function clearTimeout(){}

function settled(state,val){
  return {
    then:function(f,g){
      if(state==="ok"){
        if(typeof f!=="function") return settled(state,val);
        try{ var r=f(val); }catch(e){ return settled("err",e); }
        return (r && typeof r.then==="function") ? r : settled("ok",r);
      } else {
        if(typeof g!=="function") return settled(state,val);
        try{ var r2=g(val); }catch(e){ return settled("err",e); }
        return (r2 && typeof r2.then==="function") ? r2 : settled("ok",r2);
      }
    },
    catch:function(g){ return this.then(undefined,g); }
  };
}
var __FETCH_OK = true;
function fetch(){
  if(!__FETCH_OK) return settled("err", new Error("network"));
  return settled("ok", { ok:true, json:function(){ return settled("ok", __MOCK); } });
}
"""


def _mount(mock, fetch_ok=True, pre_js=None):
    """mock 으로 앱을 마운트한 MiniRacer 컨텍스트를 돌려준다.
    pre_js: 앱 로드 전에 평가할 JS (예: __ls 에 관심종목 사전 저장)."""
    c = MiniRacer()
    c.eval("var __MOCK = %s;" % json.dumps(mock))
    c.eval(DOM_STUB)
    # DOM_STUB 가 __FETCH_OK 를 true 로 선언하므로 반드시 스텁 뒤에 덮어쓴다
    c.eval("__FETCH_OK = %s;" % ("true" if fetch_ok else "false"))
    if pre_js:
        c.eval(pre_js)
    for fn in ("app.logic.js", "app.js"):
        with open(os.path.join(DOCS, fn), encoding="utf-8") as f:
            c.eval(f.read())
    c.eval("document._ready && document._ready();")  # init 실행
    return c


def _mock(name):
    with open(os.path.join(MOCKS, f"mock_{name}.json"), encoding="utf-8") as f:
        return json.load(f)


def _content(c):
    return c.eval("document.getElementById('content')._html")


def _click_tab(c, tab):
    c.eval("document.getElementById('tab-%s')._handlers.click()" % tab)


# ---- mock 4종 렌더 ------------------------------------------------------------
def test_render_good_s1_tab_default():
    c = _mount(_mock("good"))
    html = _content(c)
    assert "MSFT" in html and "AMZN" in html and "NVDA" in html  # pass_s1 종목
    assert "TSLA" not in html                                    # S2 전용은 S1 탭에 없음
    assert "IT/기술" in html                                       # 섹터 그룹 헤더
    assert c.eval("document.getElementById('as-of')._text") == "2026-07-01"
    assert str(c.eval("document.getElementById('count-s1')._text")) == "4"
    assert str(c.eval("document.getElementById('count-both')._text")) == "1"


def test_render_good_tab_switch_s2_and_both():
    c = _mount(_mock("good"))
    _click_tab(c, "s2")
    html = _content(c)
    assert "TSLA" in html and "AVGO" in html and "NVDA" in html
    assert "AMZN" not in html
    assert "RS(3m)" in html          # S2 카드 2행
    _click_tab(c, "both")
    html2 = _content(c)
    assert "NVDA" in html2 and "TSLA" not in html2
    assert "S1·S2" in html2          # 이중 배지


def test_render_empty_mock_shows_empty_state():
    c = _mount(_mock("empty"))
    assert "조건을 만족하는 종목이 없습니다" in _content(c)
    _click_tab(c, "s2")
    assert "종목이 없습니다" in _content(c)


def test_render_broken_mock_no_crash():
    c = _mount(_mock("broken"))
    assert "종목이 없습니다" in _content(c)  # 변조 → 빈 상태, 크래시 없음


def test_render_regime_false_banner_on_s2_tab():
    c = _mount(_mock("regime_false"))
    # S1 탭은 정상 리스트
    assert "AMGN" in _content(c)
    hidden = c.eval("document.getElementById('regime-banner').classList.contains('hidden')")
    assert hidden is True
    # S2 탭은 리스트 대신 체제 안내(정보색 — 에러 아님)
    _click_tab(c, "s2")
    assert "오늘 진입 없음" in _content(c)
    assert c.eval("document.getElementById('regime-banner').classList.contains('hidden')") is False
    banner = c.eval("document.getElementById('regime-banner')._text")
    assert "200일선 아래" in banner


def test_render_regime_null_shows_undecidable():
    m = _mock("good")
    m["regime"]["ok"] = None  # QQQ 실패 시나리오
    c = _mount(m)
    _click_tab(c, "s2")
    assert "판정불가" in _content(c)


def test_render_network_fail_no_cache_shows_error():
    c = _mount(_mock("good"), fetch_ok=False)
    hidden = c.eval("document.getElementById('error-banner').classList.contains('hidden')")
    assert hidden is False  # 에러 배너 노출


def test_refresh_button_click_spins_without_crash():
    # ⟳ 버튼: 클릭 즉시 spinning 클래스(버튼 회전)가 붙고 예외가 없어야 한다.
    # 스텁 setTimeout 은 콜백을 실행하지 않으므로 해제 타이밍은 브라우저 전용.
    c = _mount(_mock("good"))
    c.eval("document.getElementById('refresh-btn')._handlers.click()")
    assert c.eval("document.getElementById('refresh-btn').classList.contains('spinning')") is True
    # spinning 중 재클릭은 무시(연타 방지) — 예외 없이 통과해야 함
    c.eval("document.getElementById('refresh-btn')._handlers.click()")


def test_render_chart_svg_when_chart_data_present():
    m = _mock("good")
    m["items"][0]["chart"] = {"closes": [100.0, 104.0, 98.5, 110.2],
                              "start": "2026-04-01", "end": "2026-07-01"}
    c = _mount(m)
    html = _content(c)
    assert "chart-wrap" in html and "spark-line" in html
    assert "2026-04-01 ~ 2026-07-01" in html
    assert "+10.2%" in html  # 100 → 110.2 기간 등락


def test_render_chart_tampered_no_svg_no_crash():
    m = _mock("good")
    m["items"][0]["chart"] = {"closes": [100, "악성", -3]}
    c = _mount(m)
    html = _content(c)
    assert "chart-wrap" not in html          # 차트만 조용히 생략
    assert m["items"][0]["ticker"] in html   # 카드는 정상 렌더


def test_render_candle_chart_with_bands():
    m = _mock("good")
    m["items"][0]["chart"] = {
        "closes": [100, 102, 101, 110],
        "o": [99, 101, 102, 101], "h": [101, 103, 103, 111],
        "l": [98, 100, 100, 100], "c": [100, 102, 101, 110],
        "ma5": [None, 100.5, 101.0, 104.0], "ma10": [None, None, 100.8, 103.0],
        "bb_mid": [None, 101.0, 101.5, 104.0],
        "bb_up": [None, 103.0, 104.0, 112.0], "bb_lo": [None, 99.0, 99.5, 96.0],
        "start": "2026-06-27", "end": "2026-07-02",
    }
    c = _mount(m)
    html = _content(c)
    assert "<rect" in html and 'class="cu"' in html      # 양봉 존재
    assert "bb-area" in html and "BB(20,2σ)" in html     # 밴드 + 범례
    assert "spark-line" not in html                      # 라인 폴백 아님


def _click_content(c, selector, tk):
    """content 위임 클릭 시뮬레이션: selector 에만 걸리는 가짜 target 으로 호출."""
    c.eval("""
      document.getElementById('content')._handlers.click({target:{closest:function(sel){
        if(sel==='%s') return {getAttribute:function(){return '%s';}};
        return null;
      }}});
    """ % (selector, tk))


# ---- 관심종목 저장·삭제·추적 (D19) ---------------------------------------------
def test_watch_save_delete_and_date_recorded():
    c = _mount(_mock("good"))
    _click_content(c, ".star", "MSFT")  # ☆ 클릭 → 저장
    saved = json.loads(c.eval("localStorage.getItem('ndx.dual.watch.v1')"))
    assert saved[0]["ticker"] == "MSFT" and saved[0]["saved_price"] is not None
    assert len(saved[0]["saved_at"]) == 10          # YYYY-MM-DD (저장한 날짜 기록)
    assert str(c.eval("document.getElementById('count-watch')._text")) == "1"
    assert "★" in _content(c)                        # 목록 카드의 별이 채워짐
    _click_tab(c, "watch")                           # 관심 탭: 카드 + 저장일 + 삭제
    html = _content(c)
    assert "MSFT" in html and "저장 " in html and "삭제" in html
    _click_content(c, ".watch-del", "MSFT")          # 삭제 → 빈 상태 + 저장소 반영
    assert "저장한 종목이 없습니다" in _content(c)
    assert json.loads(c.eval("localStorage.getItem('ndx.dual.watch.v1')")) == []


def test_watch_tracks_dropped_ticker_via_quotes():
    # 오늘 스크리닝에 없는 저장 종목도 quotes 시세로 계속 추적된다 (기능의 핵심)
    m = _mock("good")
    m["quotes"] = {"ZZZZ": {"price": 55.0, "chg": 0.021}}
    pre = ("__ls['ndx.dual.watch.v1'] = JSON.stringify("
           "[{ticker:'ZZZZ', name:'Gone Corp', sector_kr:'미분류',"
           " saved_at:'2026-06-20', saved_price:50.0}]);")
    c = _mount(m, pre_js=pre)
    _click_tab(c, "watch")
    html = _content(c)
    assert "ZZZZ" in html and "$55" in html
    assert "2026-06-20" in html                      # 저장일 표시
    assert "+10.0%" in html                          # 50 → 55 저장 후 수익률
    assert "+2.1%" in html                           # 오늘 등락 (quotes.chg)
    assert "스크리닝 목록에는 없음" in html


def test_trade_buy_then_sell_with_price_input():
    # D20b: 버튼 → 가격 입력폼(현재 종가 기본값) → 사용자가 고친 가격으로 기록
    m = _mock("good")
    m["quotes"] = {"ZZZZ": {"price": 55.0, "chg": 0.0}}
    pre = ("__ls['ndx.dual.watch.v1'] = JSON.stringify("
           "[{ticker:'ZZZZ', name:'Gone Corp', sector_kr:'미분류',"
           " saved_at:'2026-06-20', saved_price:50.0}]);")
    c = _mount(m, pre_js=pre)
    _click_tab(c, "watch")
    assert "매수 표시" in _content(c)                 # 기록 없음 → 매수 버튼
    _click_content(c, ".trade-buy", "ZZZZ")           # 폼 오픈 (즉시 기록 아님)
    html = _content(c)
    assert "trade-form" in html and 'id="tp-ZZZZ"' in html
    assert 'value="55"' in html                       # 기본값 = 현재 종가
    saved0 = json.loads(c.eval("localStorage.getItem('ndx.dual.watch.v1')"))[0]
    assert saved0.get("buy_price") is None            # 아직 기록 전
    c.eval("document.getElementById('tp-ZZZZ').value = '50.5'")  # 가격 조절
    _click_content(c, ".trade-ok", "ZZZZ")
    saved = json.loads(c.eval("localStorage.getItem('ndx.dual.watch.v1')"))[0]
    assert saved["buy_price"] == 50.5 and len(saved["buy_at"]) == 10
    html2 = _content(c)
    assert "평가" in html2 and "+8.9%" in html2       # 55/50.5-1
    _click_content(c, ".trade-sell", "ZZZZ")          # 매도 폼
    c.eval("document.getElementById('tp-ZZZZ').value = '60'")
    _click_content(c, ".trade-ok", "ZZZZ")
    saved2 = json.loads(c.eval("localStorage.getItem('ndx.dual.watch.v1')"))[0]
    assert saved2["sell_price"] == 60.0 and len(saved2["sell_at"]) == 10
    html3 = _content(c)
    assert "확정" in html3 and "+18.8%" in html3      # 60/50.5-1
    assert "다시 매수" in html3


def test_trade_input_invalid_rejected_and_cancel():
    m = _mock("good")
    m["quotes"] = {"ZZZZ": {"price": 55.0, "chg": 0.0}}
    pre = ("__ls['ndx.dual.watch.v1'] = JSON.stringify("
           "[{ticker:'ZZZZ', name:'Gone Corp', sector_kr:'미분류',"
           " saved_at:'2026-06-20', saved_price:50.0}]);")
    c = _mount(m, pre_js=pre)
    _click_tab(c, "watch")
    _click_content(c, ".trade-buy", "ZZZZ")
    c.eval("document.getElementById('tp-ZZZZ').value = '악성'")   # 변조 입력
    _click_content(c, ".trade-ok", "ZZZZ")
    saved = json.loads(c.eval("localStorage.getItem('ndx.dual.watch.v1')"))[0]
    assert saved.get("buy_price") is None            # 거부 — 기록 안 됨
    assert "trade-form" in _content(c)               # 폼은 유지(다시 입력 기회)
    _click_content(c, ".trade-cancel", "ZZZZ")       # 취소 → 폼 닫힘, 기록 없음
    html = _content(c)
    assert "trade-form" not in html and "매수 표시" in html


def test_chart_trade_marker_lines_render():
    # 매수/매도 가격선이 봉차트 위에 표시된다 (어느 탭이든 관심 저장분 기준)
    m = _mock("good")
    tk = m["items"][0]["ticker"]
    m["items"][0]["chart"] = {
        "closes": [100, 102, 101, 110],
        "o": [99, 101, 102, 101], "h": [101, 103, 103, 111],
        "l": [98, 100, 100, 100], "c": [100, 102, 101, 110],
        "ma5": [None, 100.5, 101.0, 104.0], "ma10": [None, None, 100.8, 103.0],
        "bb_mid": [None, 101.0, 101.5, 104.0],
        "bb_up": [None, 103.0, 104.0, 112.0], "bb_lo": [None, 99.0, 99.5, 96.0],
        "start": "2026-06-27", "end": "2026-07-02",
    }
    pre = ("__ls['ndx.dual.watch.v1'] = JSON.stringify("
           "[{ticker:'%s', saved_at:'2026-06-20', saved_price:100.0,"
           " buy_price:101.0, buy_at:'2026-06-28',"
           " sell_price:150.0, sell_at:'2026-07-01'}]);" % tk)
    c = _mount(m, pre_js=pre)                        # S1 탭에서도 가격선 표시
    html = _content(c)
    assert 'class="mk-buy"' in html and "매수 101" in html
    assert 'class="mk-sell"' in html and "매도 150" in html   # 범위 밖 → 스케일 확장
    # 저장 없는 종목 차트에는 가격선 없음 (다른 카드까지 새지 않음)
    assert html.count('class="mk-buy"') == 1


def test_trade_open_position_shows_unrealized_pnl():
    # 사전 매수(50) + 현재가 55 → 평가 +10.0% / 사전 매수·매도 → 확정 -10.0%
    m = _mock("good")
    m["quotes"] = {"UPUP": {"price": 55.0, "chg": 0.01}, "DOWN": {"price": 99.0, "chg": 0.0}}
    pre = ("__ls['ndx.dual.watch.v1'] = JSON.stringify(["
           "{ticker:'UPUP', saved_at:'2026-06-20', saved_price:50.0,"
           " buy_price:50.0, buy_at:'2026-06-21'},"
           "{ticker:'DOWN', saved_at:'2026-06-20', saved_price:100.0,"
           " buy_price:100.0, buy_at:'2026-06-21', sell_price:90.0, sell_at:'2026-07-01'}]);")
    c = _mount(m, pre_js=pre)
    _click_tab(c, "watch")
    html = _content(c)
    assert "평가" in html and "+10.0%" in html        # UPUP 보유중 평가손익
    assert "확정" in html and "-10.0%" in html        # DOWN 확정손익 (현재가 99 무시)


def test_trade_tampered_fields_degrade_no_crash():
    m = _mock("good")
    m["quotes"] = {"ZZZZ": {"price": 55.0, "chg": 0.0}}
    pre = ("__ls['ndx.dual.watch.v1'] = JSON.stringify("
           "[{ticker:'ZZZZ', saved_at:'2026-06-20', saved_price:50.0,"
           " buy_price:'악성', sell_price:55.0}]);")
    c = _mount(m, pre_js=pre)
    _click_tab(c, "watch")
    html = _content(c)
    assert "ZZZZ" in html and "매수 표시" in html      # 기록 무효 → 초기 상태로 강등
    assert "확정" not in html


def test_watch_tampered_localstorage_no_crash():
    pre = "__ls['ndx.dual.watch.v1'] = '{\"악성\":true}';"
    c = _mount(_mock("good"), pre_js=pre)
    assert str(c.eval("document.getElementById('count-watch')._text")) == "0"
    _click_tab(c, "watch")
    assert "저장한 종목이 없습니다" in _content(c)


def test_render_candle_tampered_falls_back_to_line():
    m = _mock("good")
    m["items"][0]["chart"] = {
        "closes": [100.0, 104.0, 98.5, 110.2],
        "o": [99, "악성", 102, 101], "h": [1, 2, 3, 4], "l": [1, 2, 3, 4], "c": [1, 2, 3, 4],
        "start": "2026-04-01", "end": "2026-07-01",
    }
    c = _mount(m)
    html = _content(c)
    assert "spark-line" in html and "<rect" not in html  # 봉 버리고 라인 폴백
