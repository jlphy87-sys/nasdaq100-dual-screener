"""
test_schema.py — results.json 계약(§7) + 격리(§10) 검증.

- 필수 필드·타입, counts(s1/s2/both)와 items 의 pass 플래그 일관성.
- 나쁜 종목 1개가 전체를 죽이지 않음(errors[] 로 격리).
- QQQ 실패 → regime.ok=null + S2 전체 판정불가, S1 은 정상 진행.
"""

import json
import os

import numpy as np
import pandas as pd

from conftest import gc_momentum_close, make_ohlcv, pullback_recovery_df, rising_qqq
from src.build_results import build
from src.data_provider import InMemoryProvider
from src.indicators import detect_golden_cross, macd

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
    CONFIG = json.load(f)


def _s1_pass_df():
    close = gc_momentum_close()
    df = make_ohlcv(close, volume=np.full(len(close), 2e7))
    mdf = macd(df["Close"])
    _, gc_idx = detect_golden_cross(mdf["macd"], mdf["signal"], lookback_days=30)
    return df.loc[:gc_idx]


def _universe(tickers):
    return {t: {"sector": "Information Technology", "sector_kr": "IT/기술", "name": t}
            for t in tickers}


def _build(frames, tickers=None, config=None):
    provider = InMemoryProvider(frames)
    uni = _universe(tickers or [t for t in frames if t != "QQQ"])
    return build(config or CONFIG, ROOT, provider=provider, universe=uni, write=False)


def test_schema_required_fields_and_counts_consistency():
    frames = {
        "QQQ": rising_qqq(),
        "AAA": _s1_pass_df(),            # S1 통과 기대
        "BBB": pullback_recovery_df(),   # S2 통과 기대
        "CCC": make_ohlcv(np.linspace(100, 90, 300)),  # 아무것도 통과 못 함
    }
    # 3종목 표본에서 상위 25% = 1종목뿐(AAA 가 RS 1위) → 검증 목적상 50%로 완화
    config = json.loads(json.dumps(CONFIG))
    config["s2"]["rs"]["top_pct"] = 50
    r = _build(frames, config=config)

    # 필수 톱레벨 키 (§7 계약)
    for key in ["as_of", "generated_at", "stale", "universe_count", "regime",
                "counts", "config_summary", "errors_count", "sectors", "items"]:
        assert key in r, key

    assert isinstance(r["stale"], bool)
    assert r["regime"]["enabled"] is True and r["regime"]["ok"] is True
    assert set(r["counts"].keys()) == {"s1", "s2", "both"}

    # counts ↔ items 일관성
    n_s1 = sum(1 for it in r["items"] if it["pass_s1"])
    n_s2 = sum(1 for it in r["items"] if it["pass_s2"])
    n_both = sum(1 for it in r["items"] if it["pass_s1"] and it["pass_s2"])
    assert r["counts"] == {"s1": n_s1, "s2": n_s2, "both": n_both}
    assert r["counts"]["s1"] >= 1 and r["counts"]["s2"] >= 1

    # 통과 못 한 종목은 items 에 없음 (debug_show_all=false)
    assert all(it["pass_s1"] or it["pass_s2"] for it in r["items"])
    tickers = {it["ticker"] for it in r["items"]}
    assert "CCC" not in tickers

    # 아이템 필드 (§7)
    it = next(i for i in r["items"] if i["ticker"] == "AAA")
    for key in ["ticker", "name", "sector", "sector_kr", "price", "pass_s1", "pass_s2", "s1"]:
        assert key in it
    for key in ["gc_date", "slow_k", "hist", "avg_dollar_volume"]:
        assert key in it["s1"]

    it2 = next(i for i in r["items"] if i["ticker"] == "BBB")
    for key in ["rs_3m", "rs_6m", "sma50", "sma200", "drawdown", "pullback_low_pct", "trigger"]:
        assert key in it2["s2"]

    # 섹터 집계 (count_s1/count_s2)
    assert r["sectors"][0]["key"] == "IT/기술"
    assert r["sectors"][0]["count_s1"] == n_s1

    # D7: as_of 는 데이터 마지막 봉에서
    assert r["as_of"] == max(
        f.index[-1].strftime("%Y-%m-%d") for t, f in frames.items() if t != "QQQ")


def test_one_bad_ticker_isolated():
    bad = pd.DataFrame({"Wrong": [1, 2, 3]})  # 계약 위반 프레임
    frames = {"QQQ": rising_qqq(), "AAA": _s1_pass_df(), "BAD": bad}
    r = _build(frames)
    assert r["errors_count"] >= 1
    assert any(e["ticker"] == "BAD" for e in r["errors"])
    assert any(it["ticker"] == "AAA" for it in r["items"])  # 나머지는 계속


def test_qqq_failure_isolates_s2_only():
    frames = {"AAA": _s1_pass_df(), "BBB": pullback_recovery_df()}  # QQQ 없음
    r = _build(frames)
    assert r["regime"]["ok"] is None            # 판정불가 기록
    assert r["counts"]["s2"] == 0               # 임의 통과 금지(§10)
    assert r["counts"]["s1"] >= 1               # S1 은 정상 진행(격리)


def test_regime_false_zero_s2_candidates():
    from conftest import falling_qqq

    frames = {"QQQ": falling_qqq(), "BBB": pullback_recovery_df()}
    r = _build(frames)
    assert r["regime"]["ok"] is False
    assert r["counts"]["s2"] == 0  # 후보 0 이 정상 동작(에러 아님)


def test_debug_show_all_includes_failures():
    config = json.loads(json.dumps(CONFIG))
    config["debug_show_all"] = True
    frames = {"QQQ": rising_qqq(), "CCC": make_ohlcv(np.linspace(100, 90, 300))}
    r = _build(frames, config=config)
    assert any(it["ticker"] == "CCC" for it in r["items"])
