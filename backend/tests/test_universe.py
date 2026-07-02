"""
test_universe.py — 매핑 경계 테스트 (명세 §11).

심볼 정규화(.→-), ICB→GICS→한글 섹터 매핑, NDX 종목수 가드(95~110),
오버라이드 CSV 로드. 전부 네트워크 없는 순수 함수 검증.
"""

import pandas as pd

from src.universe import (
    GUARD_RANGE,
    SECTOR_COLOR,
    SECTOR_KR,
    load_override,
    normalize_symbol,
    parse_ndx,
    sector_color,
    sector_to_kr,
    within_guard,
)


# ---- 매핑: 심볼 정규화 ------------------------------------------------------
def test_normalize_symbol_dot_to_dash():
    assert normalize_symbol("BF.B") == "BF-B"
    assert normalize_symbol(" brk.b ") == "BRK-B"
    assert normalize_symbol("AAPL") == "AAPL"
    assert normalize_symbol(None) == ""


# ---- 매핑: 섹터 -------------------------------------------------------------
def test_sector_to_kr_known_and_unknown():
    assert sector_to_kr("Information Technology") == "IT/기술"
    assert sector_to_kr("Communication Services") == "커뮤니케이션"
    assert sector_to_kr("듣도보도못한섹터") == "미분류"
    assert sector_to_kr(None) == "미분류"
    assert sector_to_kr("") == "미분류"


def test_every_kr_sector_has_color():
    for kr in list(SECTOR_KR.values()) + ["미분류"]:
        assert kr in SECTOR_COLOR
        assert sector_color(kr).startswith("#")


# ---- 매핑: NDX 표 파싱 (ICB → GICS 폴백) ------------------------------------
def test_parse_ndx_icb_table():
    t = pd.DataFrame(
        {
            "Ticker": ["AAPL", "GOOG.L", "PEP"],
            "Company": ["Apple", "Alphabet", "PepsiCo"],
            "ICB Industry": ["Technology", "Telecommunications", "Consumer Staples"],
        }
    )
    out = parse_ndx([t])
    assert out["AAPL"]["sector_kr"] == "IT/기술"
    assert out["GOOG-L"]["sector_kr"] == "커뮤니케이션"  # 정규화 + ICB 매핑
    assert out["PEP"]["sector_kr"] == "필수소비재"
    assert out["AAPL"]["name"] == "Apple"


def test_parse_ndx_gics_table_passthrough():
    # 위키가 GICS 로 되돌아가도 동작(컬럼명 Sector, 값이 GICS 그대로)
    t = pd.DataFrame(
        {
            "Ticker": ["NVDA"],
            "Company": ["NVIDIA"],
            "GICS Sector": ["Information Technology"],
        }
    )
    out = parse_ndx([t])
    assert out["NVDA"]["sector_kr"] == "IT/기술"


def test_parse_ndx_unknown_icb_to_unclassified():
    t = pd.DataFrame(
        {"Ticker": ["XXX"], "Company": ["X"], "ICB Industry": ["Weird Industry"]}
    )
    assert parse_ndx([t])["XXX"]["sector_kr"] == "미분류"


# ---- 매핑: 종목수 가드 ------------------------------------------------------
def test_guard_range_spec():
    assert GUARD_RANGE == (95, 110)
    assert within_guard(101) is True
    assert within_guard(94) is False   # 이탈 → 갱신 거부
    assert within_guard(111) is False


# ---- 매핑: 오버라이드 CSV ---------------------------------------------------
def test_load_override(tmp_path):
    p = tmp_path / "universe_override.csv"
    p.write_text("ticker,sector,name\nbf.b,Consumer Staples,Brown-Forman\n", encoding="utf-8")
    out = load_override(str(p))
    assert out["BF-B"]["sector_kr"] == "필수소비재"
    assert out["BF-B"]["name"] == "Brown-Forman"
    assert load_override(str(tmp_path / "none.csv")) is None
