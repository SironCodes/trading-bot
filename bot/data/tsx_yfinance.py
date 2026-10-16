"""yfinance (unofficial): split/dividend-adjusted daily .TO bars, stamped at the 16:00 Toronto close."""
import logging
import time

import yfinance as yf
from yfinance import exceptions as yfe

from bot.data.base import COLUMNS, MarketData, empty_bars, finalize
from bot.market.calendar import close_timestamps

log = logging.getLogger(__name__)
ATTEMPTS = 3  # project rule: stop after the same API error 3 times
RETRY_WAIT_S = 5
NO_DATA = (yfe.YFPricesMissingError, yfe.YFTickerMissingError, yfe.YFTzMissingError)


class YFinanceTSX(MarketData):
    def get_bars(self, symbols, timeframe, start, end=None):
        if timeframe != "1D":
            raise NotImplementedError("v1 supports daily bars only")
        return {s: self._one(s, start, end) for s in symbols}

    def _one(self, symbol, start, end):
        span = {"start": start, "end": end} if start else {"period": "max"}
        for attempt in range(1, ATTEMPTS + 1):
            try:
                raw = yf.Ticker(symbol).history(
                    interval="1d", auto_adjust=True, actions=False, raise_errors=True, **span
                )
                break
            except NO_DATA as e:
                log.warning("%s: no data (%s)", symbol, e)
                return empty_bars()
            except Exception as e:  # rate limits and network errors: back off, then give up loudly
                if attempt == ATTEMPTS:
                    raise
                log.warning("%s: %s (attempt %d/%d)", symbol, e, attempt, ATTEMPTS)
                time.sleep(RETRY_WAIT_S * attempt)
        if raw.empty:
            return empty_bars()
        idx = raw.index.tz_localize(None) if raw.index.tz is not None else raw.index
        df = raw.rename(columns=str.lower)[COLUMNS]
        df.index = close_timestamps(idx, self.cfg.session.close, self.cfg.session.tz)
        return finalize(df)
