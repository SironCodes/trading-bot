"""MarketData: the interface every bar source implements, so a source (e.g. unofficial yfinance)
can be swapped without touching the rest of the bot."""
import datetime as dt
from abc import ABC, abstractmethod

import pandas as pd

COLUMNS = ["open", "high", "low", "close", "volume"]


class MarketData(ABC):
    """Daily OHLCV indexed by each bar's CLOSE time in UTC ("ts"). Bars that have not closed yet are
    never returned, so `bars[bars.index <= as_of]` is exactly what a decision at as_of may see."""

    def __init__(self, cfg):  # cfg: bot.config.AssetClass
        self.cfg = cfg

    @abstractmethod
    def get_bars(
        self, symbols: list[str], timeframe: str, start: dt.date | None, end: dt.date | None = None
    ) -> dict[str, pd.DataFrame]:
        """Bars per symbol from `start` (None = all history the source has). Missing symbols map to
        an empty frame."""


def empty_bars() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], tz="UTC", name="ts"), dtype="float64")


def finalize(df: pd.DataFrame, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Keep OHLCV as floats, sort, de-duplicate, and drop bars that have not closed yet."""
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    df = df[COLUMNS].astype("float64").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df.index.name = "ts"
    return df[df.index <= now]
