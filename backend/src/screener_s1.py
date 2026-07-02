"""
screener_s1.py — 스크리닝1: 반전 초기 포착 (명세 §4).

철학: "방금 위로 돌기 시작한 종목을 잡는다."
필수(AND): S1-a MACD 골든크로스 / S1-b SlowK>=min / S1-c 평균 거래대금.
선택(config, 기본 off — 켜진 것만 AND 참여):
  S1-d(D10) Close>SMA200, S1-e(D11) 교차봉 거래량 급증, S1-f(D12) ADX 최소.

각 조건은 플래그로 보존(§6)해 디버그·검증 가능하게 한다.
"""

from __future__ import annotations

import pandas as pd

from src.common import fround, last_valid, normalize_ohlcv
from src.indicators import (
    adx,
    avg_dollar_volume,
    detect_golden_cross,
    macd,
    slow_stochastic,
    sma,
    volume_ratio,
)


def evaluate_s1(df: pd.DataFrame, s1cfg: dict) -> dict | None:
    """단일 종목 OHLCV → 스크리닝1 조건 평가 dict. 데이터 부족/이상 → None(스킵).

    반환 키: last_bar_date, price, macd, signal, hist, gc_date, slow_k, slow_d,
             avg_dollar_volume, sma200, vol_ratio, adx,
             cond_gc, cond_stoch, cond_volume, cond_trend, cond_vol_surge, cond_adx,
             pass_s1.
    """
    df = normalize_ohlcv(df)
    if df is None:
        return None

    mcfg = s1cfg["macd"]
    scfg = s1cfg["stochastic"]
    vcfg = s1cfg["volume"]
    tcfg = s1cfg.get("trend", {"enabled": False})
    vscfg = s1cfg.get("volume_surge", {"enabled": False})
    acfg = s1cfg.get("adx", {"enabled": False})

    # 최소 길이: slow EMA + signal 시드
    if len(df) < mcfg["slow"] + mcfg["signal"] + 2:
        return None

    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"]

    macd_df = macd(close, mcfg["fast"], mcfg["slow"], mcfg["signal"])
    crossed, gc_idx = detect_golden_cross(
        macd_df["macd"], macd_df["signal"],
        lookback_days=mcfg.get("gc_lookback_days", 1),
        require_below_zero=mcfg.get("require_below_zero", False),
    )

    sk, sd = slow_stochastic(high, low, close, scfg["k_period"], scfg["slow_k"], scfg["slow_d"])
    adv = avg_dollar_volume(close, vol, vcfg["avg_window"])

    last_k, last_d = last_valid(sk), last_valid(sd)
    last_adv = last_valid(adv)

    # --- 필수 3조건 ---------------------------------------------------------
    cond_gc = bool(crossed)

    line, smin = scfg.get("line", "k"), scfg.get("min", 50)
    if line == "k":
        cond_stoch = last_k is not None and last_k >= smin
    elif line == "d":
        cond_stoch = last_d is not None and last_d >= smin
    else:  # both
        cond_stoch = (last_k is not None and last_k >= smin) and (
            last_d is not None and last_d >= smin)

    cond_volume = last_adv is not None and last_adv >= vcfg["min_avg_dollar_volume"]

    # --- 선택 조건 (켜진 것만 AND 참여, 꺼지면 True 로 중립) ------------------
    # S1-d (D10) — 추세 필터: Close > SMA200.
    #   이유 : 하락추세 한복판의 반등 소음을 줄인다. 비용: 초기 반전을 놓칠 수 있음.
    #   탈출구: trend.enabled(기본 false). 판정불가(SMA NaN)는 skip_if_insufficient
    #           (기본 true)면 제외 — 임의 통과 금지(§10).
    sma200_v = last_valid(sma(close, tcfg.get("sma_period", 200)))
    if tcfg.get("enabled", False):
        if sma200_v is None:
            cond_trend = not tcfg.get("skip_if_insufficient", True)
        else:
            cond_trend = float(close.iloc[-1]) > sma200_v
    else:
        cond_trend = True

    # S1-e (D11) — 거래량 급증: 판정봉 = 실제 교차가 난 봉(gc_idx).
    #   이유 : 참여 거래량이 실린 교차만 신뢰. 비용: 조용한 초기 반전 배제 가능.
    #   탈출구: volume_surge.enabled(기본 false). 교차봉을 못 찾으면 마지막 봉으로
    #           폴백(교차는 lookback 내 존재가 전제라 실제로는 드묾).
    vr_series = volume_ratio(vol, vscfg.get("vol_window", 20))
    if crossed and gc_idx is not None and gc_idx in vr_series.index:
        vr_at_bar = vr_series.loc[gc_idx]
        vr_at_bar = float(vr_at_bar) if pd.notna(vr_at_bar) else None
    else:
        vr_at_bar = last_valid(vr_series)  # 폴백: 마지막 봉
    if vscfg.get("enabled", False):
        cond_vol_surge = vr_at_bar is not None and vr_at_bar > vscfg.get("vol_mult", 1.5)
    else:
        cond_vol_surge = True

    # S1-f (D12) — ADX 최소: 추세 강도 필터.
    #   이유 : 무추세 구간의 잔교차 제거. 비용: 막 시작된 추세는 ADX 가 늦게 오른다.
    #   탈출구: adx.enabled(기본 false), min 노브.
    adx_v = last_valid(adx(high, low, close, acfg.get("period", 14)))
    if acfg.get("enabled", False):
        cond_adx = adx_v is not None and adx_v >= acfg.get("min", 20)
    else:
        cond_adx = True

    passed = bool(cond_gc and cond_stoch and cond_volume
                  and cond_trend and cond_vol_surge and cond_adx)

    return {
        "last_bar_date": df.index[-1].strftime("%Y-%m-%d"),
        "price": fround(close.iloc[-1], 2),
        "macd": fround(last_valid(macd_df["macd"]), 4),
        "signal": fround(last_valid(macd_df["signal"]), 4),
        "hist": fround(last_valid(macd_df["hist"]), 4),
        "gc_date": gc_idx.strftime("%Y-%m-%d") if (crossed and gc_idx is not None) else None,
        "slow_k": fround(last_k, 2),
        "slow_d": fround(last_d, 2),
        "avg_dollar_volume": fround(last_adv, 0),
        "sma200": fround(sma200_v, 2),
        "vol_ratio": fround(vr_at_bar, 2),
        "adx": fround(adx_v, 1),
        "cond_gc": cond_gc,
        "cond_stoch": bool(cond_stoch),
        "cond_volume": bool(cond_volume),
        "cond_trend": bool(cond_trend),
        "cond_vol_surge": bool(cond_vol_surge),
        "cond_adx": bool(cond_adx),
        "pass_s1": passed,
    }
