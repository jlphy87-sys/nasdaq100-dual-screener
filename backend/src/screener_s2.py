"""
screener_s2.py — 스크리닝2: 추세 눌림목 (명세 §5, D13~D15).

철학: "이미 강한 종목이 3~8% 쉬었다가 재시동하는 지점에 올라탄다."
층 구조(전부 AND):
  1층 D13 — 시장 체제: QQQ > SMA200 (스크린 전체 스위치)
  2층 D14 — 상대강도 리더: rs_3m>1 & rs_6m>1 + 유니버스 상위 top_pct
  3층 D15 — 구조: Close>SMA50>SMA200 정배열 + 고점대비 -15% 이내
  4층 D15 — 트리거: 3~8% 눌림 후 단기 고점 돌파 + 거래량 (또는 macd_gc 모드)

주의: 층이 4개라 통과 0~수 개가 정상(README 해석 가이드에 명시).
S2-b(상위 %)는 횡단면 조건이라 build_results 에서 유니버스 전체를 모은 뒤 판정.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.common import fround, last_valid, normalize_ohlcv
from src.indicators import (
    detect_golden_cross,
    drawdown_from_high,
    macd,
    relative_strength,
    sma,
    volume_ratio,
)


# ---------------------------------------------------------------------------
# D13 — 1층: 시장 체제 필터
# ---------------------------------------------------------------------------
def evaluate_regime(qqq_df: pd.DataFrame | None, rcfg: dict) -> dict:
    """QQQ 일봉 → regime dict {enabled, ok, qqq_close, qqq_sma200}.

    - 이유 : NDX 종목은 QQQ 상관이 높아 하락 체제의 개별 신호는 대부분 소음.
    - 비용 : 톱니장에서 신호가 늦거나 뒤집힐 수 있음(알려진 약점, README 명시).
    - 탈출구: regime.enabled(기본 true)로 끌 수 있음. sma_period 노브.
    - 불신 : QQQ 수집 실패 → ok=None(판정불가). 임의로 통과 처리 금지(§10) —
             호출부는 ok=None 이면 스크리닝2 전체를 판정불가로 격리한다.
    """
    enabled = bool(rcfg.get("enabled", True))
    out = {"enabled": enabled, "ok": None, "qqq_close": None, "qqq_sma200": None}
    if not enabled:
        out["ok"] = True  # 필터 꺼짐 = 항상 통과(명시적 선택)
        return out

    qqq = normalize_ohlcv(qqq_df)
    if qqq is None:
        return out  # ok=None → S2 판정불가

    close = qqq["Close"]
    sma_v = last_valid(sma(close, rcfg.get("sma_period", 200)))
    if sma_v is None:
        return out  # 봉 부족 → 판정불가
    last_close = float(close.iloc[-1])
    out["ok"] = bool(last_close > sma_v)
    out["qqq_close"] = fround(last_close, 2)
    out["qqq_sma200"] = fround(sma_v, 2)
    return out


# ---------------------------------------------------------------------------
# D15 — 눌림-회복 감지 (순수 함수: 테스트로 고정)
# ---------------------------------------------------------------------------
def pullback_low_pct(high: pd.Series, low: pd.Series, lookback: int = 10) -> float | None:
    """최근 lookback 봉 창에서 '고점 이후 저점'의 최대 눌림 깊이(음수).

    구현: 창 내 고가의 러닝 맥스 대비 각 봉 저가의 깊이(low/runmax-1) 중 최솟값.
    러닝 맥스를 쓰는 이유: '고점을 만든 뒤 눌린' 순서를 보장(고점 전의 저점은
    눌림이 아니다). 창에 봉이 부족하면 None(판정불가).
    """
    h = high.tail(lookback)
    l = low.tail(lookback)
    if len(h) < 2 or h.isna().all() or l.isna().all():
        return None
    runmax = h.cummax()
    with np.errstate(divide="ignore", invalid="ignore"):
        depth = l / runmax - 1.0
    depth = depth.replace([np.inf, -np.inf], np.nan).dropna()
    if depth.empty:
        return None
    return float(depth.min())


def breakout_trigger(df: pd.DataFrame, tcfg: dict) -> tuple[bool, float | None]:
    """S2-f: 마지막 봉 종가가 직전 단기 고점(최근 recent_high_bars 봉 고가 최대,
    당일 제외)을 상향 돌파 AND vol_ratio > vol_mult. (통과 여부, vol_ratio) 반환."""
    nbars = int(tcfg.get("recent_high_bars", 5))
    close, high, vol = df["Close"], df["High"], df["Volume"]
    if len(df) < nbars + 2:
        return False, None
    prior_high = high.iloc[-1 - nbars : -1].max()  # 당일 제외 직전 n봉 고가
    vr = last_valid(volume_ratio(vol, 20))
    if pd.isna(prior_high) or vr is None:
        return False, vr
    ok = float(close.iloc[-1]) > float(prior_high) and vr > tcfg.get("vol_mult", 1.3)
    return bool(ok), vr


# ---------------------------------------------------------------------------
# 종목 평가 (횡단면 조건 S2-b 제외 — build_results 에서 결합)
# ---------------------------------------------------------------------------
def evaluate_s2(df: pd.DataFrame, qqq_close: pd.Series | None, s2cfg: dict) -> dict | None:
    """단일 종목 → 스크리닝2 조건 평가 dict(S2-b 상위% 제외). 부족/이상 → None.

    반환 키: last_bar_date, price, rs_3m, rs_6m, sma50, sma200, drawdown,
             pullback_low_pct, trigger, vol_ratio,
             cond_rs_dual, cond_structure, cond_drawdown, cond_pullback, cond_trigger.
    """
    df = normalize_ohlcv(df)
    if df is None:
        return None

    rscfg = s2cfg.get("rs", {})
    stcfg = s2cfg.get("structure", {})
    pbcfg = s2cfg.get("pullback", {})
    tcfg = s2cfg.get("trigger", {})

    close, high, low = df["Close"], df["High"], df["Low"]

    # D14 / S2-a — 상대강도 (QQQ 없음 → None = 판정불가)
    short_bars = int(rscfg.get("short_bars", 63))
    long_bars = int(rscfg.get("long_bars", 126))
    rs3 = relative_strength(close, qqq_close, short_bars) if qqq_close is not None else None
    rs6 = relative_strength(close, qqq_close, long_bars) if qqq_close is not None else None
    if rscfg.get("dual_period", True):
        cond_rs_dual = rs3 is not None and rs6 is not None and rs3 > 1.0 and rs6 > 1.0
    else:
        # 탈출구(D14): dual_period=false 면 3m 만 요구
        cond_rs_dual = rs3 is not None and rs3 > 1.0

    # D15 / S2-c — 추세 구조: Close > SMA50 > SMA200 (판정불가 → False, 임의 통과 금지)
    sma_fast_v = last_valid(sma(close, stcfg.get("sma_fast", 50)))
    sma_slow_v = last_valid(sma(close, stcfg.get("sma_slow", 200)))
    last_close = float(close.iloc[-1])
    cond_structure = (
        sma_fast_v is not None and sma_slow_v is not None
        and last_close > sma_fast_v > sma_slow_v
    )

    # D15 / S2-d — 건강도: 52주 고점 대비 max_drawdown 이내
    dd = drawdown_from_high(close, window=252)
    max_dd = float(s2cfg.get("max_drawdown", -0.15))
    cond_drawdown = dd is not None and dd >= max_dd

    # D15 / S2-e — 눌림: 최근 lookback 봉 내 -min ~ -max 구간까지 눌림
    pb = pullback_low_pct(high, low, int(pbcfg.get("lookback", 10)))
    pb_min = float(pbcfg.get("min", 0.03))
    pb_max = float(pbcfg.get("max", 0.08))
    cond_pullback = pb is not None and (-pb_max <= pb <= -pb_min)

    # D15 / S2-f — 회복 트리거
    mode = tcfg.get("mode", "breakout")
    if mode == "macd_gc":
        # 탈출구(D15): 트리거 대안 — MACD 골든크로스(스크리닝1과의 비교 실험용)
        mdf = macd(close)
        crossed, _ = detect_golden_cross(mdf["macd"], mdf["signal"], lookback_days=1)
        vr = last_valid(volume_ratio(df["Volume"], 20))
        cond_trigger, trigger_name = bool(crossed), "macd_gc"
    else:
        cond_trigger, vr = breakout_trigger(df, tcfg)
        trigger_name = "breakout"

    return {
        "last_bar_date": df.index[-1].strftime("%Y-%m-%d"),
        "price": fround(last_close, 2),
        "rs_3m": fround(rs3, 4),
        "rs_6m": fround(rs6, 4),
        "sma50": fround(sma_fast_v, 2),
        "sma200": fround(sma_slow_v, 2),
        "drawdown": fround(dd, 4),
        "pullback_low_pct": fround(pb, 4),
        "trigger": trigger_name,
        "vol_ratio": fround(vr, 2),
        "cond_rs_dual": bool(cond_rs_dual),
        "cond_structure": bool(cond_structure),
        "cond_drawdown": bool(cond_drawdown),
        "cond_pullback": bool(cond_pullback),
        "cond_trigger": bool(cond_trigger),
    }


def apply_rs_top_filter(evals: dict[str, dict], top_pct: float) -> None:
    """S2-b(D14) — 횡단면: rs_3m 이 유니버스 내 상위 top_pct% 이내인지 판정해
    각 eval dict 에 cond_rs_top 을 심는다(rs_3m 없으면 False = 판정불가).

    상위 % 경계는 '유효 rs_3m 보유 종목' 기준 분위수. 동률은 포함(>=)."""
    values = sorted(
        (e["rs_3m"] for e in evals.values() if e and e.get("rs_3m") is not None),
        reverse=True,
    )
    if not values:
        for e in evals.values():
            if e:
                e["cond_rs_top"] = False
        return
    k = max(1, int(np.ceil(len(values) * float(top_pct) / 100.0)))
    threshold = values[min(k, len(values)) - 1]
    for e in evals.values():
        if not e:
            continue
        rs3 = e.get("rs_3m")
        e["cond_rs_top"] = rs3 is not None and rs3 >= threshold


def passes_s2(e: dict, regime_ok) -> bool:
    """전 층 AND. regime_ok 가 True 가 아니면(False/None) 통과 없음 — D13,
    QQQ 실패(None)는 판정불가이므로 임의 통과 금지(§10)."""
    if regime_ok is not True or not e:
        return False
    return bool(
        e.get("cond_rs_dual") and e.get("cond_rs_top")
        and e.get("cond_structure") and e.get("cond_drawdown")
        and e.get("cond_pullback") and e.get("cond_trigger")
    )
