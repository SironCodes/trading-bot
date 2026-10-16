"""Trading calendars and bar-close timestamps.

TSX holidays follow TMX's rules: the Ontario set, with weekend holidays moved to the next free
weekday. The TSX stays open on Easter Monday, June 24, Sept 30 and Nov 11. Early closes (e.g. Dec 24)
are treated as full days: a bar stamped 16:00 becomes visible later than it really closed, never earlier.
"""
import datetime as dt

import pandas as pd
from pandas.tseries.holiday import (
    MO,
    AbstractHolidayCalendar,
    GoodFriday,
    Holiday,
    next_monday,
    next_monday_or_tuesday,
)
from pandas.tseries.offsets import CustomBusinessDay, DateOffset


class TSXHolidayCalendar(AbstractHolidayCalendar):
    rules = [
        Holiday("New Year's Day", month=1, day=1, observance=next_monday),
        Holiday("Family Day", month=2, day=1, offset=DateOffset(weekday=MO(3)), start_date="2008-01-01"),
        GoodFriday,
        Holiday("Victoria Day", month=5, day=24, offset=DateOffset(weekday=MO(-1))),
        Holiday("Canada Day", month=7, day=1, observance=next_monday),
        Holiday("Civic Holiday", month=8, day=1, offset=DateOffset(weekday=MO(1))),
        Holiday("Labour Day", month=9, day=1, offset=DateOffset(weekday=MO(1))),
        Holiday("Thanksgiving", month=10, day=1, offset=DateOffset(weekday=MO(2))),
        Holiday("Christmas Day", month=12, day=25, observance=next_monday),
        Holiday("Boxing Day", month=12, day=26, observance=next_monday_or_tuesday),
    ]


def tsx_sessions(start, end) -> pd.DatetimeIndex:
    """TSX trading dates (naive midnights) from start to end inclusive."""
    return pd.date_range(start, end, freq=CustomBusinessDay(calendar=TSXHolidayCalendar()))


def close_timestamps(dates, close: dt.time, tz: str) -> pd.DatetimeIndex:
    """Naive session dates -> the UTC instant each session's bar closes."""
    local = pd.DatetimeIndex(dates).normalize() + pd.Timedelta(hours=close.hour, minutes=close.minute)
    return local.tz_localize(tz).tz_convert("UTC")


def decision_times(cfg, start, end) -> pd.DatetimeIndex:
    """UTC decision instants between two calendar dates: each TSX session (every day for 24/7 markets)
    at the asset class's decide_at time. Shared by the backtester and the runner."""
    days = tsx_sessions(start, end) if cfg.session else pd.date_range(start, end, freq="D")
    t = cfg.decide_at.time
    return (days + pd.Timedelta(hours=t.hour, minutes=t.minute)).tz_localize(cfg.decide_at.tz).tz_convert("UTC")


def latest_decision(cfg, now: pd.Timestamp) -> pd.Timestamp:
    """The most recent scheduled decision time at or before `now` (the runner's current slot)."""
    local = now.tz_convert(cfg.decide_at.tz).tz_localize(None).normalize()
    times = decision_times(cfg, local - pd.Timedelta(days=10), local)
    return times[times <= now][-1]


def expected_closes(cfg, start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    """Close stamps a complete series has between two close stamps (inclusive)."""
    if cfg.session is None:  # 24/7 market: one bar per UTC day, closing at midnight
        return pd.date_range(start, end, freq="D")
    tz = cfg.session.tz  # v1 has one exchange calendar: TSX
    days = tsx_sessions(start.tz_convert(tz).date(), end.tz_convert(tz).date())
    return close_timestamps(days, cfg.session.close, tz)
