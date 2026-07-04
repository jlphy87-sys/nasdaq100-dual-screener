"""
build_results.py — 수집→판정→results.json 산출 오케스트레이터 (명세 §6, §7).

계약(§7): results.json 의 기존 필드 의미 변경 금지, 추가만 허용(가역성).
격리(§10): 종목별 실패는 errors[] 로 기록하고 계속. QQQ 실패는 스크리닝2만
판정불가(regime.ok=null)로 격리하고 스크리닝1은 정상 진행.
"""

from __future__ import annotations

import json
import os

import pandas as pd

from src.common import is_stale, reassemble_as_of
from src.screener_s1 import evaluate_s1
from src.screener_s2 import apply_rs_top_filter, evaluate_regime, evaluate_s2, passes_s2
from src.universe import build_universe, sector_color

RESULTS_REL = os.path.join("docs", "data", "results.json")


def _config_summary(config: dict) -> dict:
    s1 = config.get("s1", {})
    s2 = config.get("s2", {})
    m = s1.get("macd", {})
    st = s1.get("stochastic", {})
    return {
        "s1": f"MACD({m.get('fast',12)}/{m.get('slow',26)}/{m.get('signal',9)}) GC "
              f"+ SlowK>={st.get('min',50)} + $vol>={s1.get('volume',{}).get('min_avg_dollar_volume',0):,}",
        "s2": f"regime(QQQ>SMA{s2.get('regime',{}).get('sma_period',200)}) + RS 상위{s2.get('rs',{}).get('top_pct',25)}% "
              f"+ 정배열 + 눌림{s2.get('pullback',{}).get('min',0.03)*100:.0f}~{s2.get('pullback',{}).get('max',0.08)*100:.0f}% 회복",
    }


def _chart_block(df, bars: int) -> dict | None:
    """통과 종목 카드용 미니 차트 데이터 (계약 §7: 필드 추가만).

    봉차트용 OHLC + 5·10일 이평 + 볼린저밴드(20, ±2σ; 중심선=20일선) 시리즈를
    서버가 계산해 싣는다 — 앱은 그리기만(의존성 0·오프라인 동작).
    지표는 전체 히스토리로 계산 후 표시 구간만 잘라 워밍업 구간에도 값이 있다.
    closes 는 구버전 앱(라인차트) 호환용으로 유지.
    비용: 통과 종목당 ~4KB. 탈출구: config.chart.bars=0 이면 생략.
    """
    if bars <= 0 or df is None:
        return None
    try:
        cols = ["Open", "High", "Low", "Close"]
        if any(k not in getattr(df, "columns", []) for k in cols):
            return None
        sub = df[cols].dropna()
        if len(sub) < 2:
            return None
        close = sub["Close"]
        ma5 = close.rolling(5).mean()
        ma10 = close.rolling(10).mean()
        ma20 = close.rolling(20).mean()
        sd20 = close.rolling(20).std(ddof=0)  # BB 표준: 모표준편차

        idx = sub.index[-bars:]

        def arr(s):
            return [round(float(v), 2) if pd.notna(v) else None for v in s.loc[idx]]

        c_arr = arr(close)
        return {
            "closes": c_arr,  # 구버전 라인차트 폴백용 (의미 유지)
            "o": arr(sub["Open"]), "h": arr(sub["High"]), "l": arr(sub["Low"]), "c": c_arr,
            "ma5": arr(ma5), "ma10": arr(ma10),
            "bb_mid": arr(ma20), "bb_up": arr(ma20 + 2 * sd20), "bb_lo": arr(ma20 - 2 * sd20),
            "start": pd.Timestamp(idx[0]).strftime("%Y-%m-%d"),
            "end": pd.Timestamp(idx[-1]).strftime("%Y-%m-%d"),
        }
    except Exception:  # noqa: BLE001 — 차트는 부가 정보: 실패해도 카드는 산다
        return None


def _quote(df) -> dict | None:
    """관심종목(폰 로컬 저장) 추적용 경량 시세 — 전 유니버스에 싣는다 (D19).

    이유: 저장한 종목이 스크리닝에서 빠진 날에도 앱이 현재가·일간 등락을
    계속 보여줘야 "추적"이 성립한다 (관심 목록은 폰 localStorage 에만 있어
    서버는 어떤 종목이 저장됐는지 모름 → 전부 싣는 수밖에 없음).
    비용: 종목당 ~40B, 전체 ~4KB. 탈출구: config.quotes.enabled=false.
    """
    try:
        closes = df["Close"].dropna()
        if len(closes) == 0:
            return None
        price = round(float(closes.iloc[-1]), 2)
        chg = None
        if len(closes) >= 2 and float(closes.iloc[-2]) > 0:
            chg = round(float(closes.iloc[-1] / closes.iloc[-2] - 1), 4)
        return {"price": price, "chg": chg}
    except Exception:  # noqa: BLE001 — 시세는 부가 정보: 실패해도 본 판정은 산다
        return None


def build(
    config: dict,
    root: str,
    provider=None,
    limit: int | None = None,
    only_tickers: list[str] | None = None,
    universe: dict | None = None,
    write: bool = True,
) -> dict:
    """전체 파이프라인 실행 → results dict 반환(+옵션으로 디스크 기록).

    provider/universe 는 테스트 주입용. limit/only_tickers 는 소규모 실검증용(§13 Phase 1).
    """
    history_days = int(config.get("history_days", 400))
    s1cfg = config["s1"]
    s2cfg = config["s2"]
    debug_show_all = bool(config.get("debug_show_all", False))
    chart_bars = int(config.get("chart", {}).get("bars", 63))  # ~3개월 (0=차트 생략)
    quotes_on = bool(config.get("quotes", {}).get("enabled", True))  # D19 관심종목 추적용

    # ---- 유니버스 (D2) ------------------------------------------------------
    warnings: list[str] = []
    if universe is None:
        paths = {
            "constituents": os.path.join(root, "backend", "data", "constituents.json"),
            "override": os.path.join(root, "backend", "data", "universe_override.csv"),
        }
        universe, warnings = build_universe(paths)

    if provider is None:
        from src.data_provider import YFinanceProvider
        provider = YFinanceProvider(os.path.join(root, "backend", "data", "cache"))

    tickers = sorted(universe.keys())
    if only_tickers:
        only = {t.upper() for t in only_tickers}
        tickers = [t for t in tickers if t in only]
    if limit:
        tickers = tickers[:limit]

    # ---- QQQ (D13 벤치마크) — 실패해도 전체를 죽이지 않는다(§10 격리) --------
    bench = s2cfg.get("regime", {}).get("benchmark", "QQQ")
    try:
        qqq_df = provider.get_history(bench, history_days)
    except Exception:  # noqa: BLE001
        qqq_df = None
    regime = evaluate_regime(qqq_df, s2cfg.get("regime", {}))
    if regime["ok"] is None and regime["enabled"]:
        warnings.append(f"{bench} 수집/판정 실패 → 스크리닝2 전체 판정불가")
    qqq_close = None
    if qqq_df is not None and "Close" in getattr(qqq_df, "columns", []):
        qqq_close = qqq_df["Close"]

    # ---- 종목별 평가 (실패 격리) ---------------------------------------------
    errors: list[dict] = []
    s1_evals: dict[str, dict] = {}
    s2_evals: dict[str, dict] = {}
    chart_src: dict[str, pd.DataFrame] = {}  # 카드 차트용 OHLC 프레임 보관
    quotes: dict[str, dict] = {}             # D19: 전 유니버스 경량 시세 (관심종목 추적)
    last_bar_dates: list[str] = []

    for ticker in tickers:
        try:
            df = provider.get_history(ticker, history_days)
            if df is None or len(df) == 0:
                errors.append({"ticker": ticker, "reason": "데이터 없음"})
                continue
            if quotes_on:
                q = _quote(df)
                if q:
                    quotes[ticker] = q
            e1 = evaluate_s1(df, s1cfg)
            e2 = evaluate_s2(df, qqq_close, s2cfg)
            if e1 is None and e2 is None:
                errors.append({"ticker": ticker, "reason": "봉 부족/정규화 실패"})
                continue
            if e1:
                s1_evals[ticker] = e1
                last_bar_dates.append(e1["last_bar_date"])
            if e2:
                s2_evals[ticker] = e2
                last_bar_dates.append(e2["last_bar_date"])
            if e1 or e2:
                chart_src[ticker] = df
        except Exception as e:  # noqa: BLE001 — 한 종목이 전체를 죽이지 않는다
            errors.append({"ticker": ticker, "reason": f"{type(e).__name__}: {e}"})

    # ---- 횡단면 조건 S2-b (D14 상위 %) --------------------------------------
    apply_rs_top_filter(s2_evals, s2cfg.get("rs", {}).get("top_pct", 25))

    # ---- 판정 결합 + items 조립 ---------------------------------------------
    items = []
    for ticker in tickers:
        e1, e2 = s1_evals.get(ticker), s2_evals.get(ticker)
        if e1 is None and e2 is None:
            continue
        meta = universe.get(ticker, {})
        pass_s1 = bool(e1 and e1.get("pass_s1"))
        pass_s2 = passes_s2(e2, regime["ok"]) if e2 else False
        if not (pass_s1 or pass_s2 or debug_show_all):
            continue
        price = (e1 or e2).get("price")
        item = {
            "ticker": ticker,
            "name": meta.get("name", ticker),
            "sector": meta.get("sector", ""),
            "sector_kr": meta.get("sector_kr", "미분류"),
            "price": price,
            "pass_s1": pass_s1,
            "pass_s2": pass_s2,
        }
        chart = _chart_block(chart_src.get(ticker), chart_bars)
        if chart:
            item["chart"] = chart
        if e1:
            item["s1"] = {
                "gc_date": e1["gc_date"], "slow_k": e1["slow_k"], "slow_d": e1["slow_d"],
                "macd": e1["macd"], "signal": e1["signal"], "hist": e1["hist"],
                "avg_dollar_volume": e1["avg_dollar_volume"], "sma200": e1["sma200"],
                "vol_ratio": e1["vol_ratio"], "adx": e1["adx"],
                "conds": {k: e1[k] for k in e1 if k.startswith("cond_")},
            }
        if e2:
            item["s2"] = {
                "rs_3m": e2["rs_3m"], "rs_6m": e2["rs_6m"],
                "sma50": e2["sma50"], "sma200": e2["sma200"],
                "drawdown": e2["drawdown"], "pullback_low_pct": e2["pullback_low_pct"],
                "trigger": e2["trigger"], "vol_ratio": e2["vol_ratio"],
                "conds": {k: e2[k] for k in e2 if k.startswith("cond_")},
            }
        items.append(item)

    # ---- 섹터 집계 (통과 종목 기준, S1/S2 별도 카운트) ------------------------
    sector_counts: dict[str, dict] = {}
    for it in items:
        key = it["sector_kr"]
        row = sector_counts.setdefault(
            key, {"key": key, "color": sector_color(key), "count_s1": 0, "count_s2": 0})
        if it["pass_s1"]:
            row["count_s1"] += 1
        if it["pass_s2"]:
            row["count_s2"] += 1
    sectors = [s for s in sector_counts.values() if s["count_s1"] or s["count_s2"] or debug_show_all]
    sectors.sort(key=lambda s: (s["count_s1"] + s["count_s2"]), reverse=True)

    # ---- 헤더 (D7: as_of 는 데이터에서 재조립, now 는 표시용) ------------------
    generated_at = pd.Timestamp.now("UTC")
    as_of = reassemble_as_of(last_bar_dates)
    n_s1 = sum(1 for it in items if it["pass_s1"])
    n_s2 = sum(1 for it in items if it["pass_s2"])
    n_both = sum(1 for it in items if it["pass_s1"] and it["pass_s2"])

    results = {
        "as_of": as_of,
        "generated_at": generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stale": is_stale(as_of, generated_at),
        "universe_count": len(universe),
        "regime": regime,
        "counts": {"s1": n_s1, "s2": n_s2, "both": n_both},
        "config_summary": _config_summary(config),
        "errors_count": len(errors),
        "errors": errors,
        "warnings": warnings,
        "sectors": sectors,
        "quotes": quotes,  # D19: 관심종목(로컬 저장) 추적용 — 계약(§7) 필드 추가
        "items": items,
    }

    if write:
        out_path = os.path.join(root, RESULTS_REL)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
    return results
