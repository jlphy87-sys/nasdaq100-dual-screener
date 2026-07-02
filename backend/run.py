"""
run.py — CLI 진입점. GitHub Actions / 로컬에서 동일하게 실행.

사용:
  python backend/run.py                    # 전체 NDX
  python backend/run.py --limit 20         # 앞 20종목(소규모 실검증, §13 Phase 1)
  python backend/run.py --tickers AAPL,MSFT
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from src.build_results import build  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--tickers", type=str, default=None)
    args = ap.parse_args()

    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        config = json.load(f)

    only = [t.strip() for t in args.tickers.split(",")] if args.tickers else None
    results = build(config, ROOT, limit=args.limit, only_tickers=only)

    c = results["counts"]
    print(f"as_of={results['as_of']} stale={results['stale']} "
          f"universe={results['universe_count']} regime_ok={results['regime']['ok']}")
    print(f"S1={c['s1']} S2={c['s2']} both={c['both']} errors={results['errors_count']}")
    for w in results.get("warnings", []):
        print(f"[warn] {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
