"""
indicators.py — MACD, 슬로우 스토캐스틱, SMA, 거래대금, 거래량비율, ADX,
상대강도(vs 벤치마크), 52주 고점 대비 하락률.

명세 §3을 정확히 구현한다. 모든 함수는 pandas Series in / Series(or tuple) out 으로
인덱스 정렬을 보존한다. 한 종목 실패가 전체를 죽이지 않도록, 데이터 부족/이상은
예외가 아니라 NaN/None 으로 흘려보낸다(스크리너가 격리 판단 — §10).

설계 결정(이유·비용·탈출구):
  - EMA 는 SMA 시드(seed) 방식(명세 §3.1).
      이유 : 동일 입력에 대해 결정적이고 손계산으로 검증 가능.
      비용 : pandas .ewm(adjust=False) 와 시드 처리가 미세하게 다를 수 있다.
      탈출구: _ema_sma_seeded 한 함수만 교체하면 전 지표가 따라온다.
  - ADX 는 Wilder 평활(첫 값 = 첫 n개 평균, 이후 (prev*(n-1)+x)/n) — §3.6.
      이유 : 교과서 정의 그대로라 수기 대조가 가능(테스트에서 tolerance 대조).
      비용 : 라이브러리(ta/pandas_ta)와 시드 관행 차이로 초반 값이 다를 수 있음.
      탈출구: _wilder_smooth 한 함수만 교체하면 ADX 전체가 따라온다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# EMA (SMA-seeded) — 명세 §3.1
# ---------------------------------------------------------------------------
def _ema_sma_seeded(values: pd.Series, n: int) -> pd.Series:
    """SMA 로 시드한 EMA. 앞쪽 NaN(예: MACD 라인의 초기 결측)은 건너뛰고
    첫 유효 구간에서 SMA(n)으로 시드한 뒤 점화식으로 전진한다.

    입력은 §10(불신 방어)에서 이미 날짜 오름차순·결측 정규화되었다고 가정한다.
    """
    arr = np.asarray(values, dtype=float)
    out = np.full(arr.shape, np.nan, dtype=float)

    valid = np.where(~np.isnan(arr))[0]
    if valid.size < n:
        return pd.Series(out, index=values.index)  # 길이 부족 → 전부 NaN

    start = int(valid[0])
    seed_idx = start + n - 1
    if seed_idx >= arr.size:
        return pd.Series(out, index=values.index)

    k = 2.0 / (n + 1.0)
    out[seed_idx] = np.mean(arr[start : seed_idx + 1])  # SMA 시드
    for t in range(seed_idx + 1, arr.size):
        out[t] = arr[t] * k + out[t - 1] * (1.0 - k)
    return pd.Series(out, index=values.index)


# ---------------------------------------------------------------------------
# MACD (12/26/9) — 명세 §3.1
# ---------------------------------------------------------------------------
def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD/Signal/Hist 를 DataFrame(columns=['macd','signal','hist'])으로 반환."""
    ema_fast = _ema_sma_seeded(close, fast)
    ema_slow = _ema_sma_seeded(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema_sma_seeded(macd_line, signal)
    hist = macd_line - signal_line
    return pd.DataFrame(
        {"macd": macd_line, "signal": signal_line, "hist": hist},
        index=close.index,
    )


def detect_golden_cross(
    macd_line: pd.Series,
    signal_line: pd.Series,
    lookback_days: int = 1,
    require_below_zero: bool = False,
):
    """gc_lookback_days 내에 골든크로스가 한 번이라도 있으면 (True, 교차일 index).
    없으면 (False, None). 교차일은 가장 최근 교차를 반환한다.

    GoldenCross(t): MACD(t-1) <= Signal(t-1) AND MACD(t) > Signal(t)
    require_below_zero=True 면 교차 시점 MACD(t) < 0 도 함께 요구.
    """
    m = macd_line.to_numpy(dtype=float)
    s = signal_line.to_numpy(dtype=float)
    idx = macd_line.index
    n = len(m)

    lookback_days = max(1, int(lookback_days))
    for t in range(n - 1, max(0, n - lookback_days) - 1, -1):
        if t - 1 < 0:
            continue
        pm, ps, cm, cs = m[t - 1], s[t - 1], m[t], s[t]
        if np.isnan(pm) or np.isnan(ps) or np.isnan(cm) or np.isnan(cs):
            continue
        if pm <= ps and cm > cs:
            if require_below_zero and not (cm < 0):
                continue
            return True, idx[t]
    return False, None


# ---------------------------------------------------------------------------
# 슬로우 스토캐스틱 (14/3/3) — 명세 §3.2
# ---------------------------------------------------------------------------
def slow_stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int = 14,
    slow_k: int = 3,
    slow_d: int = 3,
):
    """(SlowK, SlowD) 두 Series 반환.

    분모 0 가드: HH==LL 이면 RawK=50(중립). 범위 clamp: [0,100].
    """
    ll = low.rolling(k_period).min()
    hh = high.rolling(k_period).max()
    denom = hh - ll

    flat = denom == 0
    safe_denom = denom.where(~flat, other=np.nan)
    raw_k = 100.0 * (close - ll) / safe_denom
    raw_k = raw_k.where(~flat, other=50.0)

    sk = raw_k.rolling(slow_k).mean().clip(lower=0.0, upper=100.0)
    sd = sk.rolling(slow_d).mean().clip(lower=0.0, upper=100.0)

    sk.attrs["flat_window_count"] = int(flat.sum())
    sd.attrs["flat_window_count"] = int(flat.sum())
    return sk, sd


# ---------------------------------------------------------------------------
# SMA — 명세 §3.3 (n봉 미만이면 NaN = 판정불가)
# ---------------------------------------------------------------------------
def sma(values: pd.Series, n: int) -> pd.Series:
    """단순이동평균. n봉 미만 구간은 NaN(판정불가 — 임의로 통과 처리 금지)."""
    return values.rolling(n).mean()


# ---------------------------------------------------------------------------
# 거래대금 — 명세 §3.4
# ---------------------------------------------------------------------------
def avg_dollar_volume(
    close: pd.Series,
    volume: pd.Series,
    window: int = 20,
) -> pd.Series:
    """평균 달러 거래대금 = mean(Close*Volume, 최근 window 봉)."""
    return (close * volume).rolling(window).mean()


# ---------------------------------------------------------------------------
# 거래량비율 — 명세 §3.5
# ---------------------------------------------------------------------------
def volume_ratio(volume: pd.Series, window: int = 20) -> pd.Series:
    """vol_ratio(t) = Volume(t) / SMA(Volume, window)(t).
    분모 0/NaN 가드: SMA<=0 이면 NaN(판정불가)."""
    avg = volume.rolling(window).mean()
    avg = avg.where(avg > 0, other=np.nan)
    return volume / avg


# ---------------------------------------------------------------------------
# ADX(14) — 명세 §3.6 (Wilder)
# ---------------------------------------------------------------------------
def _wilder_smooth(values: np.ndarray, n: int) -> np.ndarray:
    """Wilder 평활: 첫 값 = 첫 n개 유효값의 평균, 이후 (prev*(n-1)+x)/n."""
    out = np.full(values.shape, np.nan, dtype=float)
    valid = np.where(~np.isnan(values))[0]
    if valid.size < n:
        return out
    start = int(valid[0])
    seed_idx = start + n - 1
    if seed_idx >= values.size:
        return out
    out[seed_idx] = np.nanmean(values[start : seed_idx + 1])
    for t in range(seed_idx + 1, values.size):
        x = values[t]
        if np.isnan(x):
            out[t] = out[t - 1]
        else:
            out[t] = (out[t - 1] * (n - 1) + x) / n
    return out


def adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """ADX(period). TR/+DM/-DM → Wilder 평활 → ±DI → DX → Wilder 평활 = ADX.
    가드: (+DI)+(-DI)==0 → DX=0. 결과 [0,100] clamp."""
    h = high.to_numpy(dtype=float)
    l = low.to_numpy(dtype=float)
    c = close.to_numpy(dtype=float)
    n = len(c)
    if n < 2:
        return pd.Series(np.full(n, np.nan), index=close.index)

    tr = np.full(n, np.nan)
    pdm = np.full(n, np.nan)
    ndm = np.full(n, np.nan)
    for t in range(1, n):
        tr[t] = max(h[t] - l[t], abs(h[t] - c[t - 1]), abs(l[t] - c[t - 1]))
        up = h[t] - h[t - 1]
        dn = l[t - 1] - l[t]
        pdm[t] = up if (up > dn and up > 0) else 0.0
        ndm[t] = dn if (dn > up and dn > 0) else 0.0

    atr = _wilder_smooth(tr, period)
    spdm = _wilder_smooth(pdm, period)
    sndm = _wilder_smooth(ndm, period)

    with np.errstate(divide="ignore", invalid="ignore"):
        # ATR==0(완전 횡보) → ±DI=0 으로 두면 아래 di_sum 가드가 DX=0 을 준다
        # (추세 없음 = ADX 0 이 명세 §3.6 가드의 취지).
        pdi = np.where(atr > 0, 100.0 * spdm / atr, 0.0)
        ndi = np.where(atr > 0, 100.0 * sndm / atr, 0.0)
        pdi = np.where(np.isnan(atr), np.nan, pdi)
        ndi = np.where(np.isnan(atr), np.nan, ndi)
        di_sum = pdi + ndi
        # 분모 0 가드: (+DI)+(-DI)==0 → DX=0 (명세 §3.6)
        dx = np.where(di_sum > 0, 100.0 * np.abs(pdi - ndi) / di_sum, 0.0)
        dx = np.where(np.isnan(pdi) | np.isnan(ndi), np.nan, dx)

    adx_arr = _wilder_smooth(dx, period)
    return pd.Series(np.clip(adx_arr, 0.0, 100.0), index=close.index)


# ---------------------------------------------------------------------------
# 상대강도(RS) vs 벤치마크 — 명세 §3.7
# ---------------------------------------------------------------------------
def relative_strength(
    close: pd.Series,
    bench_close: pd.Series,
    bars: int = 63,
) -> float | None:
    """rs = (Close(t)/Close(t-bars)) / (Bench(t)/Bench(t-bars)).
    마지막 봉 기준 스칼라. 봉 부족/결측/분모 0 → None(판정불가 — §10).

    벤치마크와 종목의 캘린더가 미세하게 다를 수 있어(상장휴면 등),
    날짜 인덱스 교집합으로 정렬한 뒤 계산한다(불신: 위치 정렬 금지).
    """
    if close is None or bench_close is None:
        return None
    joined = pd.concat({"c": close, "b": bench_close}, axis=1, join="inner").dropna()
    if len(joined) < bars + 1:
        return None
    c_now, c_past = float(joined["c"].iloc[-1]), float(joined["c"].iloc[-1 - bars])
    b_now, b_past = float(joined["b"].iloc[-1]), float(joined["b"].iloc[-1 - bars])
    if c_past <= 0 or b_past <= 0 or b_now <= 0:
        return None
    stock_ret = c_now / c_past
    bench_ret = b_now / b_past
    if bench_ret == 0:
        return None
    return stock_ret / bench_ret


# ---------------------------------------------------------------------------
# 고점대비 하락률 — 명세 §3.8
# ---------------------------------------------------------------------------
def drawdown_from_high(close: pd.Series, window: int = 252) -> float | None:
    """drawdown = Close(t) / max(Close, 최근 window봉) - 1. 0 이하의 음수.
    범위 검증: [-1, 0] 밖이면 데이터 이상으로 보고 None(§10)."""
    s = close.dropna()
    if s.empty:
        return None
    recent = s.tail(window)
    peak = float(recent.max())
    if peak <= 0:
        return None
    dd = float(recent.iloc[-1]) / peak - 1.0
    if dd < -1.0 or dd > 0.0 + 1e-12:
        return None  # 검증 실패(변조 방어)
    return dd
