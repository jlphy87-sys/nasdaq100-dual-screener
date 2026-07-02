"""공용 합성 데이터 빌더 (테스트 전용)."""

import numpy as np
import pandas as pd


def make_ohlcv(close, volume=None, spread=0.5, start="2025-01-01"):
    """종가 배열 → OHLCV DataFrame (High=Close+spread, Low=Close-spread)."""
    close = np.asarray(close, dtype=float)
    n = len(close)
    idx = pd.date_range(start, periods=n, freq="D")
    vol = np.asarray(volume, dtype=float) if volume is not None else np.full(n, 1e6)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + spread,
            "Low": close - spread,
            "Close": close,
            "Volume": vol,
        },
        index=idx,
    )


def v_shape_close(n_down=60, n_up=25):
    """가속 하락 → 급반등 (MACD 골든크로스 유발)."""
    t = np.arange(n_down)
    down = 100 - 0.008 * t**2
    up = down[-1] + np.linspace(0, 25, n_up)
    return np.concatenate([down, up[1:]])


def gc_momentum_close():
    """상승추세 → 얕은 눌림 → 회복 반등: 골든크로스가 SlowK 높은 지점에서 뜬다.
    (바닥 V반등의 교차는 SlowK<50 이라 S1 의 AND 를 통과 못 함 — 그게 S1-b 의 목적.)"""
    up1 = 100 + 0.5 * np.arange(80)
    pull = up1[-1] - 0.8 * np.arange(1, 9)
    rec = pull[-1] + 1.5 * np.arange(1, 26)
    return np.concatenate([up1, pull, rec])


def pullback_recovery_df(volume_spike=2.5e6):
    """S2 통과용 합성: 290봉 상승 → -6% 눌림 → 돌파+거래량 (명세 §11 '정상').

    마지막 10봉: 고점 150 → 저점 141(-6.3%) → 마지막 봉 151.5 로
    직전 5봉 고가(149)를 상향 돌파. 마지막 봉 거래량 스파이크.
    """
    trend = np.linspace(50, 150, 290)
    closes = list(trend[:-1])  # 289봉
    tail_close = [150, 147, 145, 143, 142, 143, 145, 147, 148, 151.5]
    tail_high = [150.5, 148, 146, 144, 142.5, 144, 146, 148, 149, 152]
    tail_low = [149, 146, 144, 142, 141, 142, 144, 146, 147, 148.5]
    n = len(closes) + 10
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    close = np.array(closes + tail_close)
    high = np.array([c + 0.5 for c in closes] + tail_high)
    low = np.array([c - 0.5 for c in closes] + tail_low)
    vol = np.full(n, 1e6)
    vol[-1] = volume_spike
    return pd.DataFrame(
        {"Open": close, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=idx,
    )


def rising_qqq(n=300, lo=90.0, hi=110.0):
    """QQQ>SMA200 (체제 on) 용 완만 상승 벤치마크."""
    return make_ohlcv(np.linspace(lo, hi, n))


def falling_qqq(n=300, hi=110.0, lo=70.0):
    """QQQ<SMA200 (체제 off) 용 하락 벤치마크."""
    return make_ohlcv(np.linspace(hi, lo, n))
