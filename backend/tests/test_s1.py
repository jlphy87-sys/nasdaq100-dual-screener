"""
test_s1.py — 스크리닝1(반전 초기 포착) 판정 테스트 (명세 §4, §11).

정상: 골든크로스+SlowK+거래대금 AND 통과 / 조건별 플래그 보존.
선택 노브(D10~D12): 켜면 AND 에 참여, 판정불가 처리.
None/변조: 봉 부족 → None, 음수 거래량 정규화.
"""

import copy
import json
import os

import numpy as np
import pandas as pd

from conftest import gc_momentum_close, make_ohlcv
from src.indicators import detect_golden_cross, macd
from src.screener_s1 import evaluate_s1

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
    S1CFG = json.load(f)["s1"]


def _cross_ended_df(volume=2e7):
    """골든크로스가 '마지막 봉'에서 난 합성 OHLCV (lookback=1 로 검출 가능).
    눌림-회복형이라 교차봉 SlowK 가 높다(S1-b 동시 통과 가능)."""
    close = gc_momentum_close()
    df = make_ohlcv(close, volume=np.full(len(close), volume))
    mdf = macd(df["Close"])
    crossed, gc_idx = detect_golden_cross(mdf["macd"], mdf["signal"], lookback_days=30)
    assert crossed
    return df.loc[:gc_idx]  # 교차 봉에서 시계열을 끊는다


def test_s1_all_required_pass():
    e = evaluate_s1(_cross_ended_df(), S1CFG)
    assert e is not None
    assert e["cond_gc"] is True and e["gc_date"] == e["last_bar_date"]
    assert e["cond_stoch"] is True and e["slow_k"] >= 50
    assert e["cond_volume"] is True and e["avg_dollar_volume"] >= 5e7
    # 선택 조건은 전부 off → 중립 True
    assert e["cond_trend"] is True and e["cond_vol_surge"] is True and e["cond_adx"] is True
    assert e["pass_s1"] is True


def test_s1_fails_on_low_dollar_volume():
    e = evaluate_s1(_cross_ended_df(volume=100), S1CFG)  # 거래대금 미달
    assert e["cond_gc"] is True
    assert e["cond_volume"] is False
    assert e["pass_s1"] is False  # 플래그는 보존, AND 로 탈락


def test_s1_no_cross_no_pass():
    close = np.linspace(100, 130, 80)  # 단조 상승: 최근 교차 없음
    e = evaluate_s1(make_ohlcv(close, volume=np.full(80, 2e7)), S1CFG)
    assert e["cond_gc"] is False and e["pass_s1"] is False


def test_s1_trend_knob_insufficient_excludes():
    # D10: trend on + SMA200 판정불가(봉 부족) + skip_if_insufficient=true → 제외
    cfg = copy.deepcopy(S1CFG)
    cfg["trend"]["enabled"] = True
    df = _cross_ended_df()  # ~70봉 < 200
    e = evaluate_s1(df, cfg)
    assert e["sma200"] is None
    assert e["cond_trend"] is False and e["pass_s1"] is False

    cfg["trend"]["skip_if_insufficient"] = False  # 탈출구: 판정불가 허용
    e2 = evaluate_s1(df, cfg)
    assert e2["cond_trend"] is True


def test_s1_volume_surge_knob_uses_cross_bar():
    # D11: 교차봉 거래량이 평균의 2배 → 통과 / 평균 이하 → 탈락
    cfg = copy.deepcopy(S1CFG)
    cfg["volume_surge"]["enabled"] = True
    df = _cross_ended_df()
    df.loc[df.index[-1], "Volume"] = 4e7  # 교차봉(마지막)에 스파이크
    e = evaluate_s1(df, cfg)
    assert e["cond_vol_surge"] is True and e["vol_ratio"] > 1.5

    df2 = _cross_ended_df()  # 스파이크 없음 → 비율 ~1.0
    e2 = evaluate_s1(df2, cfg)
    assert e2["cond_vol_surge"] is False and e2["pass_s1"] is False


def test_s1_adx_knob():
    cfg = copy.deepcopy(S1CFG)
    cfg["adx"]["enabled"] = True
    cfg["adx"]["min"] = 99  # 사실상 불가능한 문턱
    e = evaluate_s1(_cross_ended_df(), cfg)
    assert e["cond_adx"] is False and e["pass_s1"] is False


# ---- None/변조 --------------------------------------------------------------
def test_s1_short_series_returns_none():
    assert evaluate_s1(make_ohlcv([100.0] * 10), S1CFG) is None


def test_s1_negative_volume_normalized_not_crash():
    df = _cross_ended_df()
    df.loc[df.index[-3], "Volume"] = -5  # 변조 → NaN 처리되고 계속
    e = evaluate_s1(df, S1CFG)
    assert e is not None and isinstance(e["pass_s1"], bool)


def test_s1_duplicate_and_reversed_dates_normalized():
    df = _cross_ended_df()
    shuffled = pd.concat([df.iloc[::-1], df.tail(1)])  # 역순 + 중복 일자
    e = evaluate_s1(shuffled, S1CFG)
    ref = evaluate_s1(df, S1CFG)
    assert e["last_bar_date"] == ref["last_bar_date"]
    assert e["pass_s1"] == ref["pass_s1"]
