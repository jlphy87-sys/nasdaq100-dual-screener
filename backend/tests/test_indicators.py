"""
test_indicators.py — 지표 경계값 테스트 (명세 §11).

정상 : 합성 시계열 수기 계산과 tolerance 대조 (EMA/MACD/스토캐스틱/SMA/
       거래대금/거래량비율/ADX/RS/드로다운).
None : 봉 부족 → NaN/None (판정불가. 임의 통과 금지).
변조 : HH==LL, 0 거래량, 음수 고점 → 가드 동작.
(매핑 경계는 test_universe.py.)
"""

import numpy as np
import pandas as pd
import pytest

from src.indicators import (
    _ema_sma_seeded,
    adx,
    avg_dollar_volume,
    detect_golden_cross,
    drawdown_from_high,
    macd,
    relative_strength,
    slow_stochastic,
    sma,
    volume_ratio,
)


def _s(values):
    return pd.Series(values, index=pd.date_range("2026-01-01", periods=len(values)))


def _ohlc_from_close(close, spread=1.0):
    """Close 기준 합성 OHLC (High=Close+spread, Low=Close-spread)."""
    c = _s(close)
    return c + spread, c - spread, c


# ============================= 정상 =========================================
def test_ema_sma_seeded_hand_calc():
    # n=3, k=0.5. seed idx2 = mean(1,2,3)=2.0 → idx3 = 4*.5+2*.5=3.0 → idx4 = 5*.5+3*.5=4.0
    out = _ema_sma_seeded(_s([1, 2, 3, 4, 5]), 3)
    assert np.isnan(out.iloc[0]) and np.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(2.0)
    assert out.iloc[3] == pytest.approx(3.0)
    assert out.iloc[4] == pytest.approx(4.0)


def test_macd_hist_identity_and_shape():
    close = _s(np.linspace(100, 130, 80) + np.sin(np.arange(80)) * 2)
    df = macd(close)
    assert list(df.columns) == ["macd", "signal", "hist"]
    valid = df.dropna()
    assert len(valid) > 0
    # Hist = MACD - Signal (§3.1)
    assert np.allclose(valid["hist"], valid["macd"] - valid["signal"])


def _v_shape_close():
    """가속 하락 → 급반등 합성 종가.
    (등속 하락은 hist 가 ±1e-15 로 수렴해 부호가 불안정 — 가속 하락은 확실히 음수.)"""
    t = np.arange(60)
    down = 100 - 0.008 * t**2
    up = down[-1] + np.linspace(0, 25, 25)
    return _s(np.concatenate([down, up[1:]]))


def test_golden_cross_detected_on_v_shape():
    # 하락 후 급반등 → MACD 가 Signal 을 상향 돌파하는 지점이 생긴다
    close = _v_shape_close()
    df = macd(close)
    crossed, gc_idx = detect_golden_cross(df["macd"], df["signal"], lookback_days=30)
    assert crossed and gc_idx is not None
    # 교차일 검증: 전봉 MACD<=Signal, 당봉 MACD>Signal
    pos = df.index.get_loc(gc_idx)
    assert df["macd"].iloc[pos - 1] <= df["signal"].iloc[pos - 1]
    assert df["macd"].iloc[pos] > df["signal"].iloc[pos]


def test_golden_cross_respects_lookback():
    close = _v_shape_close()
    df = macd(close)
    _, gc_idx = detect_golden_cross(df["macd"], df["signal"], lookback_days=30)
    bars_ago = len(df) - 1 - df.index.get_loc(gc_idx)
    crossed_short, _ = detect_golden_cross(df["macd"], df["signal"], lookback_days=max(1, bars_ago - 2))
    assert crossed_short is False  # 교차가 lookback 밖이면 미검출


def test_slow_stochastic_rising_series_near_top():
    close = np.linspace(50, 100, 40)
    high, low, c = _ohlc_from_close(close, spread=0.5)
    sk, sd = slow_stochastic(high, low, c)
    assert 90 <= sk.iloc[-1] <= 100
    assert 90 <= sd.iloc[-1] <= 100


def test_sma_hand_calc_and_insufficient_nan():
    out = sma(_s([1, 2, 3, 4]), 2)
    assert np.isnan(out.iloc[0])
    assert list(out.iloc[1:]) == [1.5, 2.5, 3.5]
    # n봉 미만 → 전부 NaN (§3.3 판정불가)
    assert sma(_s([1, 2]), 5).isna().all()


def test_avg_dollar_volume_hand_calc():
    close = _s([10.0, 20.0, 30.0])
    vol = _s([100, 100, 100])
    out = avg_dollar_volume(close, vol, window=2)
    assert out.iloc[-1] == pytest.approx((20 * 100 + 30 * 100) / 2)


def test_volume_ratio_hand_calc():
    # 평균 100 인 거래량에서 마지막 봉 200 → SMA20 창 안에서 비율 > 1
    vol = _s([100.0] * 20 + [200.0])
    out = volume_ratio(vol, window=20)
    expected_avg = (100.0 * 19 + 200.0) / 20  # 마지막 창은 자기 자신 포함
    assert out.iloc[-1] == pytest.approx(200.0 / expected_avg)


def test_adx_trend_vs_chop():
    # 꾸준한 상승 추세 → ADX 높음 / 좁은 톱니 횡보 → ADX 낮음
    n = 120
    trend = np.linspace(100, 220, n)
    high_t, low_t, close_t = _ohlc_from_close(trend, spread=1.0)
    adx_trend = adx(high_t, low_t, close_t, 14)

    chop = 100 + np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
    high_c, low_c, close_c = _ohlc_from_close(chop, spread=1.5)
    adx_chop = adx(high_c, low_c, close_c, 14)

    assert adx_trend.iloc[-1] > 25
    assert adx_chop.iloc[-1] < adx_trend.iloc[-1]
    # clamp [0,100]
    assert ((adx_trend.dropna() >= 0) & (adx_trend.dropna() <= 100)).all()


def test_adx_matches_independent_reimplementation():
    """구현과 독립적인 순차 수기 계산과 tolerance 대조 (§11 '수기 계산과 대조')."""
    rng = np.random.default_rng(42)
    close = np.cumsum(rng.normal(0.3, 1.0, 100)) + 100
    high = close + rng.uniform(0.5, 1.5, 100)
    low = close - rng.uniform(0.5, 1.5, 100)
    h, l, c = _s(high), _s(low), _s(close)
    got = adx(h, l, c, 14)

    # 독립 재구현 (리스트 기반, Wilder 정의 그대로)
    n = 14
    tr, pdm, ndm = [], [], []
    for t in range(1, 100):
        tr.append(max(high[t] - low[t], abs(high[t] - close[t - 1]), abs(low[t] - close[t - 1])))
        up, dn = high[t] - high[t - 1], low[t - 1] - low[t]
        pdm.append(up if (up > dn and up > 0) else 0.0)
        ndm.append(dn if (dn > up and dn > 0) else 0.0)

    def wilder(xs):
        out = [None] * len(xs)
        out[n - 1] = sum(xs[:n]) / n
        for t in range(n, len(xs)):
            out[t] = (out[t - 1] * (n - 1) + xs[t]) / n
        return out

    atr, spdm, sndm = wilder(tr), wilder(pdm), wilder(ndm)
    dx = [None] * len(tr)
    for t in range(n - 1, len(tr)):
        pdi = 100 * spdm[t] / atr[t]
        ndi = 100 * sndm[t] / atr[t]
        dx[t] = 100 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) > 0 else 0.0
    dxs = [v for v in dx if v is not None]
    expect_last = None
    acc = sum(dxs[:n]) / n
    for t in range(n, len(dxs)):
        acc = (acc * (n - 1) + dxs[t]) / n
    expect_last = acc

    assert got.iloc[-1] == pytest.approx(expect_last, abs=1e-9)


def test_relative_strength_hand_calc():
    # 종목 2배, 벤치 불변 → rs=2.0 / 둘 다 +25% → rs=1.0
    idx = pd.date_range("2026-01-01", periods=64)
    stock = pd.Series(np.linspace(100, 200, 64), index=idx)
    bench_flat = pd.Series(100.0, index=idx)
    assert relative_strength(stock, bench_flat, bars=63) == pytest.approx(2.0)

    both = pd.Series(np.linspace(100, 125, 64), index=idx)
    assert relative_strength(both, both.copy(), bars=63) == pytest.approx(1.0)


def test_relative_strength_aligns_on_dates_not_position():
    # 벤치와 종목 캘린더가 어긋나도 날짜 교집합으로 정렬(불신: 위치 정렬 금지)
    idx_stock = pd.date_range("2026-01-01", periods=70)
    idx_bench = pd.date_range("2026-01-03", periods=70)  # 2일 어긋남
    stock = pd.Series(np.linspace(100, 200, 70), index=idx_stock)
    bench = pd.Series(100.0, index=idx_bench)
    rs = relative_strength(stock, bench, bars=63)
    assert rs is not None and rs > 1.0


def test_drawdown_hand_calc():
    close = _s([100.0] * 10 + [90.0])
    assert drawdown_from_high(close, window=252) == pytest.approx(-0.10)
    # 신고가 종목 → 0
    assert drawdown_from_high(_s(list(np.linspace(50, 100, 30))), 252) == pytest.approx(0.0)


# ============================= None/빈값 ====================================
def test_short_series_yields_nan_or_none():
    short = _s([1.0, 2.0, 3.0])
    assert macd(short)["macd"].isna().all()
    assert sma(short, 200).isna().all()
    h, l, c = _ohlc_from_close([1.0, 2.0, 3.0])
    assert adx(h, l, c, 14).isna().all()
    idx = pd.date_range("2026-01-01", periods=10)
    assert relative_strength(pd.Series(1.0, index=idx), pd.Series(1.0, index=idx), bars=63) is None


def test_empty_series_drawdown_none():
    assert drawdown_from_high(pd.Series([], dtype=float)) is None


# ============================= 변조 =========================================
def test_stochastic_flat_window_rawk_50():
    flat = [100.0] * 30
    high, low, close = _s(flat), _s(flat), _s(flat)
    sk, _ = slow_stochastic(high, low, close)
    assert sk.iloc[-1] == pytest.approx(50.0)  # HH==LL 가드
    assert sk.attrs["flat_window_count"] > 0


def test_volume_ratio_zero_volume_guard():
    out = volume_ratio(_s([0.0] * 25), window=20)
    assert out.dropna().empty  # 분모 0 → NaN(판정불가), 크래시 없음


def test_adx_flat_market_zero_not_crash():
    flat = [100.0] * 60
    h, l, c = _s(flat), _s(flat), _s(flat)
    out = adx(h, l, c, 14)
    assert out.dropna().iloc[-1] == pytest.approx(0.0)  # 추세 없음 → 0 (§3.6 가드)


def test_drawdown_rejects_nonpositive_peak():
    assert drawdown_from_high(_s([-5.0, -1.0])) is None  # 변조 데이터 → None
