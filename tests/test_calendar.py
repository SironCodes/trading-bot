import pandas as pd
import pytest

from bot.market.calendar import TSXHolidayCalendar, close_timestamps, expected_closes, tsx_sessions

# TSX closures incl. weekend observance (e.g. Christmas 2021 on Sat -> Mon 27, Boxing Day -> Tue 28).
TSX_HOLIDAYS = {
    2021: ["01-01", "02-15", "04-02", "05-24", "07-01", "08-02", "09-06", "10-11", "12-27", "12-28"],
    2022: ["01-03", "02-21", "04-15", "05-23", "07-01", "08-01", "09-05", "10-10", "12-26", "12-27"],
    2023: ["01-02", "02-20", "04-07", "05-22", "07-03", "08-07", "09-04", "10-09", "12-25", "12-26"],
    2024: ["01-01", "02-19", "03-29", "05-20", "07-01", "08-05", "09-02", "10-14", "12-25", "12-26"],
    2025: ["01-01", "02-17", "04-18", "05-19", "07-01", "08-04", "09-01", "10-13", "12-25", "12-26"],
    2026: ["01-01", "02-16", "04-03", "05-18", "07-01", "08-03", "09-07", "10-12", "12-25", "12-28"],
}


@pytest.mark.parametrize("year", sorted(TSX_HOLIDAYS))
def test_tsx_holidays(year):
    holidays = TSXHolidayCalendar().holidays(f"{year}-01-01", f"{year}-12-31")
    assert [d.strftime("%m-%d") for d in holidays] == TSX_HOLIDAYS[year]


def test_tsx_open_on_days_that_are_not_exchange_holidays():
    sessions = set(tsx_sessions("2024-01-01", "2024-12-31").strftime("%Y-%m-%d"))
    # Easter Monday, Saint-Jean-Baptiste, Truth and Reconciliation Day, Remembrance Day
    assert {"2024-04-01", "2024-06-24", "2024-09-30", "2024-11-11"} <= sessions


def test_close_stamps_follow_toronto_daylight_saving(settings):
    session = settings.asset_classes["tsx"].session
    stamps = close_timestamps(pd.DatetimeIndex(["2024-07-02", "2024-12-02"]), session.close, session.tz)
    assert list(stamps) == [pd.Timestamp("2024-07-02 20:00", tz="UTC"), pd.Timestamp("2024-12-02 21:00", tz="UTC")]


def test_expected_closes_per_asset_class(settings):
    tsx = expected_closes(settings.asset_classes["tsx"], pd.Timestamp("2024-06-28 20:00", tz="UTC"),
                          pd.Timestamp("2024-07-03 20:00", tz="UTC"))
    assert [t.date().isoformat() for t in tsx] == ["2024-06-28", "2024-07-02", "2024-07-03"]  # Canada Day closed
    crypto = expected_closes(settings.asset_classes["crypto"], pd.Timestamp("2024-06-29", tz="UTC"),
                             pd.Timestamp("2024-07-02", tz="UTC"))
    assert len(crypto) == 4 and all(t.hour == 0 for t in crypto)
