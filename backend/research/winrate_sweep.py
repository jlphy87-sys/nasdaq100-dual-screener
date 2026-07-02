"""
winrate_sweep.py — "승률을 끌어올리려면?" 스윕 실험 (D18 부속 리서치).

목적: 필터 강도를 조일 때 승률(+20일/+60일 양수 비율)이 어디까지 오르고,
신호 수가 얼마나 줄어드는지의 트레이드오프 곡선을 실측한다.

주의(과최적화 경계): 이 스윕은 '같은 과거'에 여러 조합을 대보는 것이라
여기서 고른 조합은 인샘플 튜닝이다. 목적은 최적 조합 선정이 아니라
"스크리닝만으로 도달 가능한 승률의 한계"를 보는 것.

실행: .venv/Scripts/python backend/research/winrate_sweep.py
출력: backend/research/WINRATE_SWEEP.md + 콘솔.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from src.indicators import sma, volume_ratio  # noqa: E402
from backtest import (  # noqa: E402
    FETCH_DAYS, HORIZONS, fetch_all, forward_returns, load_universe,
    s1_signals, s2_parts,
)

START_EVAL = "2021-01-01"


def rich_parts(df: pd.DataFrame, qqq_close: pd.Series, cfg: dict) -> pd.DataFrame:
    """변형 조립용 원재료: S2 구성값 + 보조 시리즈를 한 번에."""
    s2 = s2_parts(df, qqq_close, cfg["s2"])  # rs_3m, conds_ex_top(기본 파라미터)
    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"]

    sma50 = sma(close, 50).ffill()
    sma200 = sma(close, 200).ffill()
    peak = close.rolling(252, min_periods=1).max()
    dd = close / peak - 1.0

    lookback = int(cfg["s2"]["pullback"]["lookback"])
    cols = []
    for w in range(1, lookback + 1):
        cols.append((low / high.rolling(w).max() - 1.0).shift(lookback - w))
    pb = pd.concat(cols, axis=1).min(axis=1)

    nbars = int(cfg["s2"]["trigger"]["recent_high_bars"])
    prior_high = high.shift(1).rolling(nbars).max()
    vr = volume_ratio(vol, 20).ffill()

    joined = pd.concat({"c": close, "b": qqq_close}, axis=1, join="inner").dropna()
    rs6 = ((joined["c"] / joined["c"].shift(126))
           / (joined["b"] / joined["b"].shift(126))).reindex(df.index)

    return pd.DataFrame({
        "rs3": s2["rs_3m"], "rs6": rs6,
        "struct": ((close > sma50) & (sma50 > sma200)).fillna(False),
        "trend200": (close > sma200).fillna(False),
        "dd": dd, "pb": pb,
        "brk": (close > prior_high).fillna(False), "vr": vr,
        "s1": s1_signals(df, cfg["s1"]),
    }, index=df.index)


def top_mask(rs3_wide: pd.DataFrame, top_pct: float) -> pd.DataFrame:
    out = pd.DataFrame(False, index=rs3_wide.index, columns=rs3_wide.columns)
    arr = rs3_wide.to_numpy()
    for i in range(arr.shape[0]):
        valid = arr[i][~np.isnan(arr[i])]
        if valid.size == 0:
            continue
        k = max(1, int(np.ceil(valid.size * top_pct / 100.0)))
        thr = np.sort(valid)[::-1][min(k, valid.size) - 1]
        out.iloc[i] = arr[i] >= thr
    return out


def stats(mask_wide: pd.DataFrame, fwd: dict, label: str) -> dict:
    rows = []
    for tk in mask_wide.columns:
        col = mask_wide[tk]
        dates = col.index[col & (col.index >= START_EVAL)]
        # 합집합 인덱스의 날짜가 개별 종목엔 없을 수 있음(늦은 상장) → 교집합
        dates = dates.intersection(fwd[tk].index)
        if len(dates):
            rows.append(fwd[tk].loc[dates])
    out = {"label": label, "n": 0}
    if not rows:
        return out
    sig = pd.concat(rows, ignore_index=True)
    out["n"] = len(sig)
    for h in (20, 60):
        r = sig[f"ret_{h}"].dropna()
        e = sig[f"exc_{h}"].dropna()
        p = (r > 0).mean() if len(r) else np.nan
        out[f"win{h}"] = p
        out[f"ci{h}"] = 1.96 * np.sqrt(p * (1 - p) / len(r)) if len(r) else np.nan
        out[f"ret{h}"] = r.mean() if len(r) else np.nan
        out[f"exc{h}"] = e.mean() if len(e) else np.nan
    return out


def main() -> int:
    with open(ROOT.parent / "config.json", encoding="utf-8") as f:
        cfg = json.load(f)
    cache = ROOT / "data" / "cache" / "backtest"

    tickers = load_universe()
    frames = fetch_all(tickers + ["QQQ"], cache)
    qqq = frames.pop("QQQ")
    print(f"데이터 {len(frames)}종목 (캐시)", flush=True)

    P = {tk: rich_parts(df, qqq["Close"], cfg) for tk, df in frames.items()}
    fwd = {tk: forward_returns(df, qqq) for tk, df in frames.items()}

    def wide(col):
        return pd.DataFrame({tk: p[col] for tk, p in P.items()})

    rs3_w, rs6_w = wide("rs3"), wide("rs6")
    struct_w, trend_w = wide("struct").fillna(False), wide("trend200").fillna(False)
    dd_w, pb_w, vr_w = wide("dd"), wide("pb"), wide("vr")
    brk_w, s1_w = wide("brk").fillna(False), wide("s1").fillna(False)

    top25, top10 = top_mask(rs3_w, 25), top_mask(rs3_w, 10)
    rs_dual = ((rs3_w > 1.0) & (rs6_w > 1.0)).fillna(False)

    qqq_sma200 = (qqq["Close"] > sma(qqq["Close"], 200)).fillna(False)
    qqq_sma50 = (qqq["Close"] > sma(qqq["Close"], 50)).fillna(False)

    def reg(series):
        return pd.DataFrame({tk: series.reindex(s1_w.index).fillna(False)
                             for tk in s1_w.columns})

    reg200, reg50 = reg(qqq_sma200), reg(qqq_sma200 & qqq_sma50)

    pb_ok = ((pb_w <= -0.03) & (pb_w >= -0.08)).fillna(False)
    dd15, dd08 = (dd_w >= -0.15).fillna(False), (dd_w >= -0.08).fillna(False)
    vr13, vr20 = (vr_w > 1.3).fillna(False), (vr_w > 2.0).fillna(False)

    s2_core = rs_dual & struct_w & pb_ok & brk_w  # 공통 골격

    variants = [
        ("S2 기본 (현행)",                s2_core & top25 & dd15 & vr13 & reg200),
        ("S2 + RS 상위10%",              s2_core & top10 & dd15 & vr13 & reg200),
        ("S2 + 고점대비 -8% 이내",        s2_core & top25 & dd08 & vr13 & reg200),
        ("S2 + 돌파거래량 2.0×",          s2_core & top25 & dd15 & vr20 & reg200),
        ("S2 + 체제 강화(QQQ>SMA50&200)", s2_core & top25 & dd15 & vr13 & reg50),
        ("S2 강화 콤보(위 4개 전부)",      s2_core & top10 & dd08 & vr20 & reg50),
        ("겹침 S1∩S2 (현행)",            s2_core & top25 & dd15 & vr13 & reg200 & s1_w),
        ("S1 기본 (현행)",                s1_w),
        ("S1 + 추세필터 D10(Close>SMA200)", s1_w & trend_w),
        ("S1 + D10 + 체제(QQQ>SMA200)",   s1_w & trend_w & reg200),
    ]

    results = [stats(m, fwd, lbl) for lbl, m in variants]

    # 기준선: 모든 종목·모든 날 진입(무필터)의 승률 — 필터 기여도의 비교 원점
    all_days = pd.DataFrame(True, index=s1_w.index, columns=s1_w.columns)
    base = stats(all_days, fwd, "기준선: 무필터(전 종목·전일)")
    results.append(base)

    L = ["# 승률 스윕 — 필터 강도 vs 승률/신호수 트레이드오프\n",
         f"- 기간 {START_EVAL}~, 진입/청산·한계는 BACKTEST.md 와 동일",
         "- ±값은 승률의 95% 신뢰구간 — **신호수가 줄면 구간이 커져 숫자를 믿을 수 없게 된다**",
         "- ★이 표에서 고른 조합은 인샘플 튜닝이다. '한계 확인'용이지 '설정 추천'이 아니다.\n",
         "| 변형 | 신호수 | 승률+20일 | 승률+60일 | 평균+20일 | QQQ대비+20일 |",
         "|---|---|---|---|---|---|"]
    for r in results:
        if r["n"] == 0:
            L.append(f"| {r['label']} | 0 | — | — | — | — |")
            continue
        L.append(f"| {r['label']} | {r['n']} "
                 f"| {r['win20']*100:.0f}% ±{r['ci20']*100:.0f} "
                 f"| {r['win60']*100:.0f}% ±{r['ci60']*100:.0f} "
                 f"| {r['ret20']*100:+.2f}% | {r['exc20']*100:+.2f}% |")
        print(f"{r['label']:38s} n={r['n']:>6} win20={r['win20']*100:5.1f}%±{r['ci20']*100:.1f} "
              f"win60={r['win60']*100:5.1f}% ret20={r['ret20']*100:+.2f}% exc20={r['exc20']*100:+.2f}%")

    with open(Path(__file__).with_name("WINRATE_SWEEP.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n보고서: backend/research/WINRATE_SWEEP.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
