"""
data_provider.py — 일봉 OHLCV 데이터 소스 추상화.

설계 결정 D1 — 데이터 소스: yfinance(일봉).
  이유 : 무료, API 키 불필요(키 노출 리스크 0). 서버측 실행이라 CORS 없음.
  비용 : 비공식 API → 가끔 레이트리밋/장애. 재시도·캐시로 방어.
  탈출구: DataProvider 인터페이스로 추상화 → 소스 교체 시 구현 클래스만 갈아끼움.

QQQ(스크리닝2 체제·RS 벤치마크)도 같은 인터페이스로 수집한다(그냥 티커 하나).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

import pandas as pd

# OHLCV DataFrame 계약: index=DatetimeIndex(오름차순), columns 최소 {Open,High,Low,Close,Volume}
OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


class DataProvider(ABC):
    """일봉 OHLCV 공급자 인터페이스.

    get_history 는 ticker 의 최근 `history_days` 캘린더일 일봉을 반환한다.
    데이터가 없거나 부족하면 None/빈 DataFrame(예외로 전체 중단 금지 — §10 격리).
    """

    @abstractmethod
    def get_history(self, ticker: str, history_days: int) -> pd.DataFrame | None:
        ...


class InMemoryProvider(DataProvider):
    """테스트/합성용. {ticker: OHLCV DataFrame} 사전을 그대로 돌려준다."""

    def __init__(self, frames: dict[str, pd.DataFrame]):
        self._frames = frames

    def get_history(self, ticker: str, history_days: int = 400) -> pd.DataFrame | None:
        df = self._frames.get(ticker)
        if df is None:
            return None
        return df.tail(history_days) if history_days else df


class YFinanceProvider(DataProvider):
    """yfinance 기반 일봉 공급자 + 로컬 일배치 캐시 + 재시도/백오프 (§2 공통, D1).

    결정(이유·비용·탈출구):
      - auto_adjust=True (분할/배당 보정 종가).
          이유 : 액면분할 불연속이 MACD/RS 가짜 신호를 만드는 걸 막는다.
          비용 : 달러 거래대금이 보정가 기준이라 명목값과 미세 차이.
          탈출구: 이 한 줄(auto_adjust)만 바꾸면 원시가로 환원.
      - 캐시 키 {TICKER}_{YYYYMMDD}: 같은 날 재실행은 디스크에서 즉시 재사용.
      - 종목별 실패는 예외를 삼키고 None 반환 → 한 종목이 전체를 죽이지 않는다.
    """

    def __init__(self, cache_dir: str, retries: int = 3, backoff: float = 1.5,
                 run_date: str | None = None):
        import datetime as _dt

        self.cache_dir = cache_dir
        self.retries = retries
        self.backoff = backoff
        # 캐시 버스팅용 날짜(D7 의 as_of 와 무관 — as_of 는 데이터에서 재조립)
        self.run_date = run_date or _dt.datetime.now().strftime("%Y%m%d")
        os.makedirs(cache_dir, exist_ok=True)

    def _cache_path(self, ticker: str) -> str:
        return os.path.join(self.cache_dir, f"{ticker}_{self.run_date}.csv")

    def get_history(self, ticker: str, history_days: int = 400) -> pd.DataFrame | None:
        cache_path = self._cache_path(ticker)
        if os.path.exists(cache_path):
            try:
                df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
                if not df.empty:
                    return df
            except Exception:
                pass  # 캐시 손상 → 재수집

        import time

        import yfinance as yf

        last_err = None
        for attempt in range(self.retries):
            try:
                df = yf.Ticker(ticker).history(
                    period=f"{history_days}d", interval="1d", auto_adjust=True
                )
                if df is None or df.empty:
                    last_err = "empty"
                else:
                    df = df[OHLCV_COLUMNS].copy()
                    df.index = pd.to_datetime(df.index).tz_localize(None)
                    df.to_csv(cache_path)
                    return df
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {e}"
            time.sleep(self.backoff ** attempt)  # 지수 백오프
        return None  # 최종 실패 → 스킵 (호출부가 errors[] 기록)
