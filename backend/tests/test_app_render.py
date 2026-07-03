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


def _mount(mock, fetch_ok=True):
    """mock 으로 앱을 마운트한 MiniRacer 컨텍스트를 돌려준다."""
    c = MiniRacer()
    c.eval("var __MOCK = %s;" % json.dumps(mock))
    c.eval(DOM_STUB)
    # DOM_STUB 가 __FETCH_OK 를 true 로 선언하므로 반드시 스텁 뒤에 덮어쓴다
    c.eval("__FETCH_OK = %s;" % ("true" if fetch_ok else "false"))
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
