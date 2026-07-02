"""
universe.py — Nasdaq-100 유니버스 + 섹터(한글) 구성.

설계 결정 D2 — 유니버스: 위키피디아 Nasdaq-100 표 + 캐시(data/constituents.json).
  불신 가드: 종목 수 95~110 밖이면 갱신 거부 + 캐시 유지 + 경고.
  수동 오버라이드 data/universe_override.csv(ticker,sector[,name]) 최우선.
  심볼 정규화 . → - (테스트로 고정).
설계 결정 D8 — 섹터: GICS 영문 → 한글 라벨 + 고정 색.

분기 발견 / 결정(이유·비용·탈출구):
  명세 D2 는 위키 표의 GICS 섹터를 가정했으나, 현재 Nasdaq-100 위키 표는
  ICB 분류(ICB Industry 컬럼)를 쓴다.
  결정: ICB → GICS 1:1 폴백 매핑(아래 ICB_TO_GICS)으로 사상. 실패는 '미분류'.
  이유 : 한 번의 스크랩으로 유니버스+섹터 확보(종목별 info 호출 없음 = D2 취지).
  비용 : ICB/GICS 경계가 다른 소수 종목(일부 미디어 등)이 어긋날 수 있다.
  탈출구: universe_override.csv 가 있으면 최우선 적용(수동 교정 가능).
"""

from __future__ import annotations

import csv
import io
import json
import os

# D8 — GICS 영문 섹터 → 한글 라벨. 색은 앱 배지에서 일관 사용.
SECTOR_KR: dict[str, str] = {
    "Information Technology": "IT/기술",
    "Health Care": "헬스케어",
    "Financials": "금융",
    "Consumer Discretionary": "자유소비재",
    "Communication Services": "커뮤니케이션",
    "Industrials": "산업재",
    "Consumer Staples": "필수소비재",
    "Energy": "에너지",
    "Utilities": "유틸리티",
    "Real Estate": "부동산",
    "Materials": "소재",
}

SECTOR_COLOR: dict[str, str] = {
    "IT/기술": "#3B82F6",
    "헬스케어": "#10B981",
    "금융": "#6366F1",
    "자유소비재": "#F59E0B",
    "커뮤니케이션": "#EC4899",
    "산업재": "#64748B",
    "필수소비재": "#14B8A6",
    "에너지": "#EF4444",
    "유틸리티": "#A855F7",
    "부동산": "#84CC16",
    "소재": "#F97316",
    "미분류": "#9CA3AF",
}

UNCLASSIFIED = "미분류"

ICB_TO_GICS: dict[str, str] = {
    "Technology": "Information Technology",
    "Health Care": "Health Care",
    "Financials": "Financials",
    "Consumer Discretionary": "Consumer Discretionary",
    "Consumer Staples": "Consumer Staples",
    "Industrials": "Industrials",
    "Energy": "Energy",
    "Utilities": "Utilities",
    "Telecommunications": "Communication Services",
    "Real Estate": "Real Estate",
    "Basic Materials": "Materials",
}

# D2 불신 가드: 스크랩 종목 수가 이 범위 밖이면 스크랩 오류 → 갱신 거부.
GUARD_RANGE: tuple[int, int] = (95, 110)

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
NDX_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"


def normalize_symbol(symbol: str) -> str:
    """심볼 정규화(매핑 경계): Yahoo 표기로. '.' → '-'. 공백 제거, 대문자화."""
    if symbol is None:
        return ""
    return str(symbol).strip().upper().replace(".", "-")


def sector_to_kr(sector_en: str | None) -> str:
    """GICS 영문 섹터 → 한글 라벨. 누락/미지정은 '미분류'."""
    if not sector_en:
        return UNCLASSIFIED
    return SECTOR_KR.get(str(sector_en).strip(), UNCLASSIFIED)


def sector_color(sector_kr: str) -> str:
    return SECTOR_COLOR.get(sector_kr, SECTOR_COLOR[UNCLASSIFIED])


def _find_col(columns, *needles) -> str | None:
    """컬럼명 중 needles 를 (대소문자 무시) 모두 포함하는 첫 컬럼."""
    for c in columns:
        cs = str(c)
        if all(n.lower() in cs.lower() for n in needles):
            return c
    return None


def _pick_table(tables, *required_needle_groups):
    for t in tables:
        cols = list(t.columns)
        if all(_find_col(cols, *grp) is not None for grp in required_needle_groups):
            return t
    return None


def parse_ndx(tables) -> dict[str, dict]:
    """Nasdaq-100 위키 표 → {정규화 심볼: {sector, sector_kr, name}}. 순수 함수.
    ICB Industry → ICB_TO_GICS 로 GICS 사상(실패 → 미분류)."""
    t = _pick_table(tables, ("Ticker",), ("Company",), ("Industry",))
    if t is None:
        t = _pick_table(tables, ("Ticker",), ("Company",), ("Sector",))
    if t is None:
        raise ValueError("Nasdaq-100 구성 표를 찾지 못함(구조 변경 가능)")
    tick_c = _find_col(t.columns, "Ticker")
    name_c = _find_col(t.columns, "Company")
    ind_c = _find_col(t.columns, "Industry") or _find_col(t.columns, "Sector")
    out: dict[str, dict] = {}
    for _, r in t.iterrows():
        raw = str(r[tick_c]).strip()
        if not raw or raw.lower() == "nan":
            continue
        ticker = normalize_symbol(raw)
        icb = str(r[ind_c]).strip()
        # GICS 표라면 그대로, ICB 표라면 매핑(둘 다 SECTOR_KR/ICB_TO_GICS 로 흡수)
        gics = icb if icb in SECTOR_KR else ICB_TO_GICS.get(icb, "")
        out[ticker] = {
            "sector": gics,
            "sector_kr": sector_to_kr(gics),
            "name": str(r[name_c]).strip() or ticker,
        }
    return out


def _http_tables(url: str):
    """requests(브라우저 UA)로 받아 read_html. 기본 urllib 은 위키에서 403."""
    import pandas as pd
    import requests

    r = requests.get(url, headers=_UA, timeout=30)
    r.raise_for_status()
    return pd.read_html(io.StringIO(r.text))


def within_guard(count: int) -> bool:
    lo, hi = GUARD_RANGE
    return lo <= count <= hi


def load_override(path: str) -> dict | None:
    """universe_override.csv(ticker,sector[,name]) → universe dict. 없으면 None."""
    if not path or not os.path.exists(path):
        return None
    out: dict[str, dict] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ticker = normalize_symbol(row.get("ticker") or row.get("Ticker") or "")
            if not ticker:
                continue
            sector_en = (row.get("sector") or row.get("Sector") or "").strip()
            out[ticker] = {
                "sector": sector_en,
                "sector_kr": sector_to_kr(sector_en),
                "name": (row.get("name") or row.get("Name") or ticker).strip(),
            }
    return out or None


def _save_constituents(path: str, universe: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(universe, f, ensure_ascii=False, indent=2)


def _load_constituents(path: str) -> dict | None:
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_universe(paths: dict, force_refresh: bool = False) -> tuple[dict, list]:
    """NDX 유니버스 최종 조립 + 캐시 + 불신 가드(D2).

    우선순위: override 파일 > 신선 스크랩(가드 통과) > 캐시.
    반환: ({ticker: {sector, sector_kr, name}}, warnings[]).
    """
    warnings: list[str] = []
    constituents_path = paths["constituents"]
    override_path = paths.get("override")

    # 1) 수동 오버라이드 최우선 (D2 탈출구)
    override = load_override(override_path) if override_path else None
    if override:
        warnings.append(f"universe_override 사용: {len(override)}종목")
        _save_constituents(constituents_path, override)
        return override, warnings

    # 2) 신선 스크랩 + 가드
    try:
        universe = parse_ndx(_http_tables(NDX_URL))
        if not within_guard(len(universe)):
            raise ValueError(f"Nasdaq-100 종목수 가드 이탈: {len(universe)}")
        _save_constituents(constituents_path, universe)
        return universe, warnings
    except Exception as e:  # noqa: BLE001 — 스크랩 실패는 전체를 죽이지 않는다(D2)
        warnings.append(f"유니버스 스크랩 실패→캐시 사용: {type(e).__name__}: {e}")
        cached = _load_constituents(constituents_path)
        if cached:
            return cached, warnings
        raise RuntimeError("스크랩 실패 + 캐시 없음 — 유니버스를 구성할 수 없음") from e
