"""
common.py — 두 스크리너가 공유하는 방어 유틸(§10 불신 기반).

OHLCV 정규화(오름차순·중복 제거·음수 가드), as_of 재조립(D7), stale 판정,
마지막 유효값/반올림 헬퍼.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame | None:
    """외부 입력 불신 정규화: 날짜 오름차순 강제, 중복 일자 제거(마지막 유지),
    0/음수 가격 → NaN, 음수 거래량 → NaN, NaN 종가 행 제거. 불능이면 None."""
    if df is None or len(df) == 0:
        return None
    need = {"Open", "High", "Low", "Close", "Volume"}
    if not need.issubset(set(df.columns)):
        return None
    out = df.copy()
    out.index = pd.to_datetime(out.index)
    out = out[~out.index.duplicated(keep="last")]
    out = out.sort_index()
    for col in ["Open", "High", "Low", "Close"]:
        out.loc[out[col] <= 0, col] = np.nan
    out.loc[out["Volume"] < 0, "Volume"] = np.nan
    out = out.dropna(subset=["Close"])
    return out if len(out) else None


def reassemble_as_of(last_bar_dates: list[str]) -> str | None:
    """D7 — 수집된 종목들의 마지막 봉 날짜 중 최신을 보고서 기준일로 재조립.
    (시스템 시계 불신: now() 는 표시용 generated_at 에서만 쓴다.)"""
    valid = [d for d in last_bar_dates if d]
    return max(valid) if valid else None


def is_stale(as_of: str, generated_at_utc: pd.Timestamp, max_age_days: int = 5) -> bool:
    """마지막 봉이 기대보다 오래되면 stale. 주말·휴장 여유로 기본 5일."""
    if not as_of:
        return True
    gen = generated_at_utc
    if gen.tzinfo is not None:  # tz-aware(UTC) → naive 통일(pandas 3.x 혼합 연산 금지)
        gen = gen.tz_localize(None)
    age = (gen.normalize() - pd.Timestamp(as_of).normalize()).days
    return age > max_age_days


def last_valid(series: pd.Series):
    """Series 의 마지막 유효값(float). 없으면 None."""
    s = series.dropna()
    if s.empty:
        return None
    v = float(s.iloc[-1])
    return v if not math.isnan(v) else None


def fround(v, ndigits):
    """None/NaN 안전 반올림."""
    if v is None:
        return None
    try:
        if math.isnan(v):
            return None
    except TypeError:
        return None
    return round(float(v), ndigits)
