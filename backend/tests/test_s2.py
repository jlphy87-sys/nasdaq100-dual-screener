"""
test_s2.py — 스크리닝2(추세 눌림목) 판정 테스트 (명세 §5, §11).

정상: '고점→-5%눌림→돌파+거래량' 합성이 전 층 통과 / '-20% 붕괴'는 S2-d 탈락.
체제: QQQ<SMA200 → regime.ok=False → 후보 0.
None: QQQ 실패 → ok=None(판정불가), 임의 통과 금지.
"""

import copy
import json
import os

import numpy as np
import pytest

from conftest import falling_qqq, make_ohlcv, pullback_recovery_df, rising_qqq
from src.screener_s2 import (
    apply_rs_top_filter,
    breakout_trigger,
    evaluate_regime,
    evaluate_s2,
    passes_s2,
    pullback_low_pct,
)

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
    S2CFG = json.load(f)["s2"]


# ---- D13: 체제 --------------------------------------------------------------
def test_regime_ok_when_qqq_above_sma200():
    r = evaluate_regime(rising_qqq(), S2CFG["regime"])
    assert r["ok"] is True
    assert r["qqq_close"] > r["qqq_sma200"]


def test_regime_false_when_qqq_below_sma200():
    r = evaluate_regime(falling_qqq(), S2CFG["regime"])
    assert r["ok"] is False  # 에러가 아니라 정상 동작(오늘 진입 없음)


def test_regime_none_when_qqq_missing_or_short():
    assert evaluate_regime(None, S2CFG["regime"])["ok"] is None
    assert evaluate_regime(make_ohlcv([100.0] * 50), S2CFG["regime"])["ok"] is None


def test_regime_disabled_always_ok():
    cfg = dict(S2CFG["regime"], enabled=False)
    assert evaluate_regime(None, cfg)["ok"] is True  # 탈출구(D13)


# ---- D15: 눌림/트리거 순수 함수 ----------------------------------------------
def test_pullback_low_pct_hand_calc():
    df = pullback_recovery_df()
    pb = pullback_low_pct(df["High"], df["Low"], 10)
    assert pb == pytest.approx(141 / 150.5 - 1, abs=1e-9)  # 약 -6.3%


def test_breakout_trigger_pass_and_volume_gate():
    df = pullback_recovery_df()
    ok, vr = breakout_trigger(df, S2CFG["trigger"])
    assert ok is True and vr > 1.3
    quiet = pullback_recovery_df(volume_spike=1e6)  # 거래량 스파이크 없음
    ok2, vr2 = breakout_trigger(quiet, S2CFG["trigger"])
    assert ok2 is False  # 가격 돌파해도 거래량 미달 → 탈락


# ---- 전 층 통합 ---------------------------------------------------------------
def _eval_full(df, qqq=None, cfg=None):
    qqq = qqq if qqq is not None else rising_qqq()
    e = evaluate_s2(df, qqq["Close"], cfg or S2CFG)
    apply_rs_top_filter({"T": e}, (cfg or S2CFG)["rs"]["top_pct"])
    return e


def test_s2_pullback_recovery_passes_all_layers():
    e = _eval_full(pullback_recovery_df())
    assert e["cond_rs_dual"] and e["cond_rs_top"]
    assert e["cond_structure"] and e["cond_drawdown"]
    assert e["cond_pullback"] and e["cond_trigger"]
    assert passes_s2(e, regime_ok=True) is True


def test_s2_collapse_20pct_fails_drawdown():
    # -20% 붕괴: 상승 후 20% 하락 유지 → S2-d 탈락 (명세 §11)
    trend = np.linspace(50, 150, 280)
    crash = np.linspace(150, 118, 20)  # -21%
    df = make_ohlcv(np.concatenate([trend, crash]))
    e = _eval_full(df)
    assert e["cond_drawdown"] is False
    assert passes_s2(e, regime_ok=True) is False


def test_s2_regime_false_blocks_everything():
    e = _eval_full(pullback_recovery_df())  # 종목 자체는 전 층 통과
    assert passes_s2(e, regime_ok=False) is False   # D13 스위치
    assert passes_s2(e, regime_ok=None) is False    # QQQ 실패 = 판정불가(§10)


def test_s2_weak_rs_fails_dual():
    # 종목이 벤치보다 약함 → S2-a 탈락
    df = pullback_recovery_df()
    strong_bench = make_ohlcv(np.linspace(50, 400, 299))  # 벤치가 훨씬 강함
    e = evaluate_s2(df, strong_bench["Close"], S2CFG)
    assert e["cond_rs_dual"] is False


def test_s2_rs_single_period_knob():
    # 탈출구(D14): dual_period=false 면 rs_3m 만 요구
    cfg = copy.deepcopy(S2CFG)
    cfg["rs"]["dual_period"] = False
    e = _eval_full(pullback_recovery_df(), cfg=cfg)
    assert e["cond_rs_dual"] is True


def test_s2_no_pullback_fails():
    # 눌림 없이 신고가 직행 → S2-e 탈락
    df = make_ohlcv(np.linspace(50, 150, 300))
    e = _eval_full(df)
    assert e["cond_pullback"] is False
    assert passes_s2(e, regime_ok=True) is False


# ---- 횡단면: 상위 % ----------------------------------------------------------
def test_rs_top_pct_cross_sectional():
    evals = {f"T{i}": {"rs_3m": 1.0 + i * 0.01} for i in range(100)}  # 1.00~1.99
    apply_rs_top_filter(evals, 25)
    top = [t for t, e in evals.items() if e["cond_rs_top"]]
    assert len(top) == 25
    assert "T99" in top and "T0" not in top


def test_rs_top_pct_none_rs_never_top():
    evals = {"A": {"rs_3m": 1.5}, "B": {"rs_3m": None}}
    apply_rs_top_filter(evals, 50)
    assert evals["B"]["cond_rs_top"] is False  # 판정불가 → 임의 통과 금지


def test_s2_short_series_returns_none():
    assert evaluate_s2(make_ohlcv([100.0] * 3), rising_qqq()["Close"], S2CFG) is None
