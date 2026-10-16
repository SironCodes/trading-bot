"""Alpaca crypto 1Day bars (they open at 00:00 UTC); historical crypto data needs no API keys."""
import datetime as dt

import pandas as pd
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, CryptoLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame

from bot.data.base import COLUMNS, MarketData, empty_bars, finalize

EARLIEST = dt.datetime(2015, 1, 1, tzinfo=dt.UTC)  # before Alpaca's first crypto bar (2021-01-01)


def _utc(day: dt.date | None) -> dt.datetime | None:
    return None if day is None else dt.datetime.combine(day, dt.time(), tzinfo=dt.UTC)


class AlpacaCrypto(MarketData):
    def get_bars(self, symbols, timeframe, start, end=None):
        if timeframe != "1D":
            raise NotImplementedError("v1 supports daily bars only")
        req = CryptoBarsRequest(
            symbol_or_symbols=list(symbols), timeframe=TimeFrame.Day, start=_utc(start) or EARLIEST, end=_utc(end)
        )
        raw = CryptoHistoricalDataClient().get_crypto_bars(req).df  # alpaca-py retries 429s itself
        out = {s: empty_bars() for s in symbols}
        if raw.empty:
            return out
        for sym, g in raw.groupby(level="symbol"):
            df = g.droplevel("symbol")[COLUMNS]
            df.index = df.index + pd.Timedelta(days=1)  # stamp each bar at its close
            out[sym] = finalize(df)
        return out

    def latest_quotes(self, symbols) -> dict[str, tuple[float, float]]:
        """{symbol: (bid, ask)} right now, for live limit prices."""
        quotes = CryptoHistoricalDataClient().get_crypto_latest_quote(
            CryptoLatestQuoteRequest(symbol_or_symbols=list(symbols)))
        return {s: (float(q.bid_price), float(q.ask_price)) for s, q in quotes.items() if q.bid_price and q.ask_price}
