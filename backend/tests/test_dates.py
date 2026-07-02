"""
test_dates.py — D7: as_of 는 시스템 시계가 아니라 데이터 마지막 봉에서 재조립.
stale 판정은 as_of 와 generated_at 의 거리 기준.
"""

import pandas as pd

from src.common import is_stale, reassemble_as_of


def test_as_of_is_max_of_last_bar_dates_not_clock():
    # 시스템 오늘이 무엇이든, 데이터가 6/27 까지면 as_of=6/27
    assert reassemble_as_of(["2026-06-26", "2026-06-27", "2026-06-25"]) == "2026-06-27"


def test_as_of_none_when_no_data():
    assert reassemble_as_of([]) is None
    assert reassemble_as_of([None, ""]) is None


def test_stale_flag():
    gen = pd.Timestamp("2026-07-02T03:00:00", tz="UTC")
    assert is_stale("2026-07-01", gen) is False
    assert is_stale("2026-06-25", gen) is True     # 7일 경과 > 5일
    assert is_stale(None, gen) is True             # as_of 없음 = stale


def test_stale_accepts_naive_timestamp():
    # pandas 3.x: tz-aware/naive 혼합 연산 금지 → 내부에서 통일하는지
    assert is_stale("2026-07-01", pd.Timestamp("2026-07-02")) is False
