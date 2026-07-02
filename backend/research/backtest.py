"""
backtest.py — 스크리닝1·2 조건의 과거 성과 검토 (이벤트 스터디).

설계 결정 D18 — 백테스트는 리서치 전용, 제품 밖.
  이유 : 명세 §1 이 백테스트를 앱 OUT 스코프로 선언 — 앱/스키마/파이프라인은
         건드리지 않고, 조건의 과거 유효성만 별도로 검토한다(사용자 요청).
  비용 : 포지션 사이징·비용·슬리피지 없는 '신호 후 수익률' 관찰이라
         실계좌 성과와 다르다. 통계이지 수익 보증이 아니다.
  탈출구: 이 디렉터리를 지워도 제품은 무손상. 조건 변경 실험은 config 사본으로.

방법:
  - 유니버스: 현재 NDX 구성종목(backend/data/constituents.json) + QQQ.
    ★생존편향: 과거 편입/퇴출 이력이 없어 '지금 살아남은 종목'에 소급 적용
    → 결과가 실제보다 낙관적일 수 있음(보고서에 명시).
  - 신호: 운영 지표 함수(src.indicators)를 그대로 재사용해 전 기간 벡터화.
    판정불가(NaN)는 False — 임의 통과 금지(§10)와 동일 원칙.
  - 정합성(불신 방어): 무작위 (종목,날짜) 표본에 대해 운영 evaluate_s1/s2 를
    해당 날짜까지 데이터로 직접 실행, 벡터화 신호와 100% 일치해야 통과.
    (운영은 400캘린더일 창을 쓰므로 EMA 시드 오차가 있을 수 있어, 대조는
     '같은 시작점' 슬라이스로 수행 — 벡터화 로직 자체의 검증이 목적.)
  - 성과: 신호일 종가 확정 후 다음 거래일 시가 진입, +N 거래일 종가 청산.
    N ∈ {5,10,20,60}. 초과수익 = 종목수익 - QQQ 동일구간 수익.

실행: .venv/Scripts/python backend/research/backtest.py [--start-eval 2021-01-01]
출력: backend/research/BACKTEST.md + 콘솔 요약. 신호 CSV 는 캐시(gitignore)에.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]  # backend/
sys.path.insert(0, str(ROOT))

from src.common import normalize_ohlcv  # noqa: E402
from src.data_provider import YFinanceProvider  # noqa: E402
from src.indicators import macd, slow_stochastic, sma, volume_ratio  # noqa: E402
from src.screener_s1 import evaluate_s1  # noqa: E402
from src.screener_s2 import evaluate_s2  # noqa: E402

HORIZONS = [5, 10, 20, 60]
FETCH_DAYS = 2740  # 약 7.5년 캘린더일: 2021 평가 시작 전 워밍업(SMA200+52주+RS6m) 확보


# ---------------------------------------------------------------------------
# 데이터
# ---------------------------------------------------------------------------
def load_universe() -> list[str]:
    with open(ROOT / "data" / "constituents.json", encoding="utf-8") as f:
        data = json.load(f)
    return sorted(data["tickers"].keys()) if "tickers" in data else sorted(data.keys())


def fetch_all(tickers: list[str], cache_dir: Path) -> dict[str, pd.DataFrame]:
    provider = YFinanceProvider(str(cache_dir))
    frames: dict[str, pd.DataFrame] = {}
    for i, tk in enumerate(tickers, 1):
        df = provider.get_history(tk, history_days=FETCH_DAYS)
        norm = normalize_ohlcv(df) if df is not None else None
        if norm is not None and len(norm) >= 60:
            frames[tk] = norm
        if i % 20 == 0:
            print(f"  fetch {i}/{len(tickers)}", flush=True)
    return frames


# ---------------------------------------------------------------------------
# 벡터화 신호 (운영 로직의 시계열 확장 — 지표 함수는 운영 코드 재사용)
# ---------------------------------------------------------------------------
def s1_signals(df: pd.DataFrame, s1cfg: dict) -> pd.Series:
    """S1 필수 3조건(AND)의 일자별 통과 여부. 선택 노브는 운영 기본값(off)."""
    mcfg, scfg, vcfg = s1cfg["macd"], s1cfg["stochastic"], s1cfg["volume"]
    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"]

    mdf = macd(close, mcfg["fast"], mcfg["slow"], mcfg["signal"])
    m, s = mdf["macd"], mdf["signal"]
    # gc_lookback_days=1: 교차가 '그날' 발생 (운영 detect_golden_cross 와 동일 부등호)
    cond_gc = (m.shift(1) <= s.shift(1)) & (m > s)
    if mcfg.get("require_below_zero", False):
        cond_gc &= m < 0

    sk, sd = slow_stochastic(high, low, close, scfg["k_period"], scfg["slow_k"], scfg["slow_d"])
    line = scfg.get("line", "k")
    smin = scfg.get("min", 50)
    # 운영 last_valid = 마지막 유효값 → 시계열에선 ffill 이 동치
    kf, dfil = sk.ffill(), sd.ffill()
    if line == "k":
        cond_stoch = kf >= smin
    elif line == "d":
        cond_stoch = dfil >= smin
    else:
        cond_stoch = (kf >= smin) & (dfil >= smin)

    adv = (close * vol).rolling(vcfg["avg_window"]).mean().ffill()
    cond_volume = adv >= vcfg["min_avg_dollar_volume"]

    return (cond_gc & cond_stoch & cond_volume).fillna(False)


def s2_parts(df: pd.DataFrame, qqq_close: pd.Series, s2cfg: dict) -> pd.DataFrame:
    """S2 종목별 조건(횡단면 rs_top 제외)의 일자별 시계열 + rs_3m 값."""
    rscfg = s2cfg.get("rs", {})
    stcfg = s2cfg.get("structure", {})
    pbcfg = s2cfg.get("pullback", {})
    tcfg = s2cfg.get("trigger", {})
    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"]

    # S2-a RS: 운영 relative_strength 와 동일 — 날짜 교집합 정렬 후 bars 시프트 비율
    joined = pd.concat({"c": close, "b": qqq_close}, axis=1, join="inner").dropna()
    sb, lb = int(rscfg.get("short_bars", 63)), int(rscfg.get("long_bars", 126))
    rs3 = (joined["c"] / joined["c"].shift(sb)) / (joined["b"] / joined["b"].shift(sb))
    rs6 = (joined["c"] / joined["c"].shift(lb)) / (joined["b"] / joined["b"].shift(lb))
    rs3 = rs3.reindex(df.index)
    rs6 = rs6.reindex(df.index)
    if rscfg.get("dual_period", True):
        cond_rs_dual = (rs3 > 1.0) & (rs6 > 1.0)
    else:
        cond_rs_dual = rs3 > 1.0

    # S2-c 구조: Close > SMA50 > SMA200
    sma_f = sma(close, stcfg.get("sma_fast", 50)).ffill()
    sma_s = sma(close, stcfg.get("sma_slow", 200)).ffill()
    cond_structure = (close > sma_f) & (sma_f > sma_s)

    # S2-d 고점대비: 운영 drawdown_from_high(tail 252 최대 대비) == rolling max
    peak = close.rolling(252, min_periods=1).max()
    dd = close / peak - 1.0
    cond_drawdown = dd >= float(s2cfg.get("max_drawdown", -0.15))

    # S2-e 눌림: 창 내 '러닝맥스 대비 저가' 최소값 (운영 pullback_low_pct 의 창별 확장)
    lookback = int(pbcfg.get("lookback", 10))
    cols = []
    for w in range(1, lookback + 1):
        x = low / high.rolling(w).max() - 1.0
        cols.append(x.shift(lookback - w))
    pb = pd.concat(cols, axis=1).min(axis=1)
    pb_min, pb_max = float(pbcfg.get("min", 0.03)), float(pbcfg.get("max", 0.08))
    cond_pullback = (pb <= -pb_min) & (pb >= -pb_max)

    # S2-f 트리거(breakout): 당일 제외 직전 n봉 고가 돌파 + vol_ratio
    nbars = int(tcfg.get("recent_high_bars", 5))
    prior_high = high.shift(1).rolling(nbars).max()
    vr = volume_ratio(vol, 20).ffill()
    cond_trigger = (close > prior_high) & (vr > float(tcfg.get("vol_mult", 1.3)))

    return pd.DataFrame({
        "rs_3m": rs3,
        "conds_ex_top": (cond_rs_dual & cond_structure & cond_drawdown
                         & cond_pullback & cond_trigger).fillna(False),
    }, index=df.index)


def regime_series(qqq: pd.DataFrame, rcfg: dict) -> pd.Series:
    """D13 체제: QQQ Close > SMA(sma_period). NaN(워밍업) = False(판정불가≠통과)."""
    if not rcfg.get("enabled", True):
        return pd.Series(True, index=qqq.index)
    s = sma(qqq["Close"], rcfg.get("sma_period", 200))
    return (qqq["Close"] > s).fillna(False)


def rs_top_mask(rs3_wide: pd.DataFrame, top_pct: float) -> pd.DataFrame:
    """S2-b 횡단면: 일자별 rs_3m 상위 top_pct% (운영 apply_rs_top_filter 와 동일:
    k=ceil(n*pct/100), k번째 값 이상 = 동률 포함)."""
    out = pd.DataFrame(False, index=rs3_wide.index, columns=rs3_wide.columns)
    arr = rs3_wide.to_numpy()
    for i in range(arr.shape[0]):
        row = arr[i]
        valid = row[~np.isnan(row)]
        if valid.size == 0:
            continue
        k = max(1, int(np.ceil(valid.size * top_pct / 100.0)))
        thr = np.sort(valid)[::-1][min(k, valid.size) - 1]
        out.iloc[i] = row >= thr
    return out


# ---------------------------------------------------------------------------
# 정합성 검증 (불신 방어): 벡터화 신호 vs 운영 evaluate_* 표본 대조
# ---------------------------------------------------------------------------
def consistency_check(frames, qqq_close, s1_wide, s2ex_wide, cfg, n_samples=120,
                      seed=42) -> tuple[int, int]:
    rng = random.Random(seed)
    tickers = [t for t in frames if len(frames[t]) > 320]
    mismatch = 0
    for _ in range(n_samples):
        tk = rng.choice(tickers)
        df = frames[tk]
        i = rng.randrange(300, len(df))
        date = df.index[i]
        sub = df.iloc[: i + 1]

        e1 = evaluate_s1(sub, cfg["s1"])
        v1 = bool(s1_wide.at[date, tk]) if date in s1_wide.index else False
        p1 = bool(e1["pass_s1"]) if e1 else False
        if p1 != v1:
            mismatch += 1
            print(f"  [S1 불일치] {tk} {date.date()} 운영={p1} 벡터={v1}")

        e2 = evaluate_s2(sub, qqq_close.loc[:date], cfg["s2"])
        conds2 = bool(e2 and e2["cond_rs_dual"] and e2["cond_structure"]
                      and e2["cond_drawdown"] and e2["cond_pullback"] and e2["cond_trigger"])
        v2 = bool(s2ex_wide.at[date, tk]) if date in s2ex_wide.index else False
        if conds2 != v2:
            mismatch += 1
            print(f"  [S2 불일치] {tk} {date.date()} 운영={conds2} 벡터={v2}")
    return n_samples * 2, mismatch


# ---------------------------------------------------------------------------
# 성과 측정
# ---------------------------------------------------------------------------
def forward_returns(df: pd.DataFrame, qqq: pd.DataFrame) -> pd.DataFrame:
    """신호일 t 기준: 진입 = Open[t+1], 청산 = Close[t+N]. QQQ 동일구간 병기."""
    out = pd.DataFrame(index=df.index)
    entry = df["Open"].shift(-1)
    q_entry = qqq["Open"].shift(-1)
    for n in HORIZONS:
        out[f"ret_{n}"] = df["Close"].shift(-n) / entry - 1.0
        qret = (qqq["Close"].shift(-n) / q_entry - 1.0).reindex(df.index)
        out[f"exc_{n}"] = out[f"ret_{n}"] - qret
    return out


def collect_signals(mask_wide: pd.DataFrame, fwd: dict[str, pd.DataFrame],
                    start_eval: str) -> pd.DataFrame:
    rows = []
    for tk in mask_wide.columns:
        col = mask_wide[tk]
        dates = col.index[col & (col.index >= start_eval)]
        if len(dates) == 0:
            continue
        f = fwd[tk].loc[dates]
        f = f.assign(ticker=tk, date=dates)
        rows.append(f)
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


def summarize(sig: pd.DataFrame, label: str) -> dict:
    out = {"screen": label, "signals": len(sig),
           "tickers": sig["ticker"].nunique() if len(sig) else 0}
    for n in HORIZONS:
        r, e = sig[f"ret_{n}"].dropna(), sig[f"exc_{n}"].dropna()
        out[f"ret{n}_mean"] = r.mean() if len(r) else np.nan
        out[f"ret{n}_med"] = r.median() if len(r) else np.nan
        out[f"win{n}"] = (r > 0).mean() if len(r) else np.nan
        out[f"exc{n}_mean"] = e.mean() if len(e) else np.nan
        out[f"exc{n}_med"] = e.median() if len(e) else np.nan
        out[f"excwin{n}"] = (e > 0).mean() if len(e) else np.nan
    return out


def per_year(sig: pd.DataFrame) -> pd.DataFrame:
    if sig.empty:
        return pd.DataFrame()
    g = sig.assign(year=pd.to_datetime(sig["date"]).dt.year).groupby("year")
    return pd.DataFrame({
        "신호수": g.size(),
        "20d수익_평균": g["ret_20"].mean(),
        "20d승률": g["ret_20"].apply(lambda x: (x.dropna() > 0).mean()),
        "20d초과_평균": g["exc_20"].mean(),
        "20d초과승률": g["exc_20"].apply(lambda x: (x.dropna() > 0).mean()),
    })


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-eval", default="2021-01-01")
    ap.add_argument("--samples", type=int, default=120)
    args = ap.parse_args()

    with open(ROOT.parent / "config.json", encoding="utf-8") as f:
        cfg = json.load(f)

    cache_dir = ROOT / "data" / "cache" / "backtest"
    tickers = load_universe()
    print(f"유니버스 {len(tickers)}종목 + QQQ, {FETCH_DAYS}일 수집…", flush=True)
    frames = fetch_all(tickers + ["QQQ"], cache_dir)
    qqq = frames.pop("QQQ", None)
    if qqq is None:
        print("QQQ 수집 실패 — 중단")
        return 1
    print(f"수집 완료: {len(frames)}종목, QQQ {qqq.index[0].date()}~{qqq.index[-1].date()}")

    # --- 신호 계산 ---------------------------------------------------------
    s1_cols, s2ex_cols, rs3_cols, fwd = {}, {}, {}, {}
    for tk, df in frames.items():
        s1_cols[tk] = s1_signals(df, cfg["s1"])
        parts = s2_parts(df, qqq["Close"], cfg["s2"])
        s2ex_cols[tk] = parts["conds_ex_top"]
        rs3_cols[tk] = parts["rs_3m"]
        fwd[tk] = forward_returns(df, qqq)

    s1_wide = pd.DataFrame(s1_cols).fillna(False)
    s2ex_wide = pd.DataFrame(s2ex_cols).fillna(False)
    rs3_wide = pd.DataFrame(rs3_cols)

    top = rs_top_mask(rs3_wide, float(cfg["s2"]["rs"].get("top_pct", 25)))
    reg = regime_series(qqq, cfg["s2"].get("regime", {}))
    reg_wide = pd.DataFrame({tk: reg.reindex(s2ex_wide.index).fillna(False)
                             for tk in s2ex_wide.columns})

    s2_wide = s2ex_wide & top & reg_wide
    s2_noreg_wide = s2ex_wide & top
    both_wide = s1_wide & s2_wide

    # --- 정합성 검증 --------------------------------------------------------
    print(f"정합성 검증: 표본 {args.samples}×2 대조…", flush=True)
    total, bad = consistency_check(frames, qqq["Close"], s1_wide, s2ex_wide,
                                   cfg, args.samples)
    print(f"  일치 {total - bad}/{total}")
    if bad:
        print("  ★불일치 발견 — 결과 신뢰 불가, 원인 수정 필요")
        return 2

    # --- 성과 집계 ----------------------------------------------------------
    sigs = {
        "S1 (반전 초기)": collect_signals(s1_wide, fwd, args.start_eval),
        "S2 (추세 눌림목)": collect_signals(s2_wide, fwd, args.start_eval),
        "S2 (체제필터 제외)": collect_signals(s2_noreg_wide, fwd, args.start_eval),
        "겹침 (S1∩S2)": collect_signals(both_wide, fwd, args.start_eval),
    }
    summaries = [summarize(s, k) for k, s in sigs.items()]

    # QQQ 기준선: 같은 기간 매일 진입했을 때의 평균 (신호의 초과가치 비교용)
    q_fwd = forward_returns(qqq, qqq)
    q_eval = q_fwd.loc[q_fwd.index >= args.start_eval]
    baseline = {n: q_eval[f"ret_{n}"].mean() for n in HORIZONS}

    write_report(summaries, sigs, baseline, args.start_eval, qqq, total)
    sigs["S1 (반전 초기)"].to_csv(cache_dir / "signals_s1.csv", index=False)
    sigs["S2 (추세 눌림목)"].to_csv(cache_dir / "signals_s2.csv", index=False)

    for s in summaries:
        print(f"\n[{s['screen']}] 신호 {s['signals']}건 / {s['tickers']}종목")
        for n in HORIZONS:
            print(f"  +{n:>2}일: 수익 평균 {s[f'ret{n}_mean']*100:+.2f}% "
                  f"(승률 {s[f'win{n}']*100:.0f}%) · "
                  f"QQQ대비 {s[f'exc{n}_mean']*100:+.2f}% "
                  f"(초과승률 {s[f'excwin{n}']*100:.0f}%)")
    print("\n보고서: backend/research/BACKTEST.md")
    return 0


def write_report(summaries, sigs, baseline, start_eval, qqq, n_checked) -> None:
    end = qqq.index[-1].date()
    L = []
    L.append("# 스크리닝 조건 백테스트 (이벤트 스터디)\n")
    L.append(f"- 기간: **{start_eval} ~ {end}** (신호 평가 기준, 워밍업 별도)")
    L.append("- 유니버스: 현재 NDX 구성종목 소급 적용 (★생존편향 — 아래 한계 참조)")
    L.append("- 진입/청산: 신호일 종가 확정 → **다음 거래일 시가 진입**, +N 거래일 종가 청산")
    L.append("- 초과수익: 동일 구간 QQQ 수익률 차감. 거래비용·슬리피지 미반영")
    L.append(f"- 정합성: 무작위 표본 {n_checked}건에서 운영 코드(evaluate_s1/s2)와 "
             "벡터화 신호 **100% 일치** 확인\n")

    L.append("## 결과 요약\n")
    L.append("| 스크린 | 신호수 | 종목수 | +5일 | +10일 | +20일 | +60일 |")
    L.append("|---|---|---|---|---|---|---|")
    for s in summaries:
        cells = " | ".join(
            f"{s[f'ret{n}_mean']*100:+.2f}% / {s[f'exc{n}_mean']*100:+.2f}%"
            if pd.notna(s[f"ret{n}_mean"]) else "—"
            for n in HORIZONS)
        L.append(f"| {s['screen']} | {s['signals']} | {s['tickers']} | {cells} |")
    L.append("\n(셀 = 평균수익 / QQQ대비 평균초과. 아래 상세표에 승률 포함)\n")

    bl = " · ".join(f"+{n}일 {baseline[n]*100:+.2f}%" for n in HORIZONS)
    L.append(f"기준선(같은 기간 QQQ 평균): {bl}\n")

    for s in summaries:
        L.append(f"### {s['screen']} — 상세\n")
        L.append("| 구간 | 평균수익 | 중앙값 | 승률 | 평균초과(QQQ) | 초과중앙값 | 초과승률 |")
        L.append("|---|---|---|---|---|---|---|")
        for n in HORIZONS:
            if pd.isna(s[f"ret{n}_mean"]):
                L.append(f"| +{n}일 | — | — | — | — | — | — |")
                continue
            L.append(
                f"| +{n}일 | {s[f'ret{n}_mean']*100:+.2f}% | {s[f'ret{n}_med']*100:+.2f}% "
                f"| {s[f'win{n}']*100:.0f}% | {s[f'exc{n}_mean']*100:+.2f}% "
                f"| {s[f'exc{n}_med']*100:+.2f}% | {s[f'excwin{n}']*100:.0f}% |")
        L.append("")

    for key in ("S1 (반전 초기)", "S2 (추세 눌림목)"):
        py = per_year(sigs[key])
        if py.empty:
            continue
        L.append(f"### {key} — 연도별 (+20일 기준)\n")
        L.append("| 연도 | 신호수 | 평균수익 | 승률 | QQQ대비 | 초과승률 |")
        L.append("|---|---|---|---|---|---|")
        for y, r in py.iterrows():
            L.append(f"| {y} | {int(r['신호수'])} | {r['20d수익_평균']*100:+.2f}% "
                     f"| {r['20d승률']*100:.0f}% | {r['20d초과_평균']*100:+.2f}% "
                     f"| {r['20d초과승률']*100:.0f}% |")
        L.append("")

    L.append("## 해석 시 한계 (반드시 함께 읽기)\n")
    L.append("1. **생존편향**: 현재 살아남은 NDX 종목에 조건을 소급 — 퇴출 종목이 빠져 "
             "결과가 실제보다 낙관적일 수 있음.")
    L.append("2. **비용 미반영**: 수수료·슬리피지·세금 0 가정.")
    L.append("3. **중복 신호**: 같은 종목이 며칠 연속 신호를 낼 수 있고 각각 1건으로 "
             "집계(이벤트 스터디 관례). 포트폴리오 수익률과 다름.")
    L.append("4. **EMA 시드**: 운영은 400일 창, 백테스트는 장기 시계열로 MACD 시드가 "
             "달라 경계 사례에서 신호가 미세하게 다를 수 있음(수렴 후 차이 ~0).")
    L.append("5. **과최적화 경계**: 이 결과를 보고 노브를 조정하면 그 시점부터 "
             "인샘플 튜닝이 된다. 조정 후에는 미래 구간으로만 재검증할 것.")

    with open(Path(__file__).with_name("BACKTEST.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
