import pandas as pd
from conftest import daily_bars

from bot.data.quality import check
from bot.market.calendar import close_timestamps, tsx_sessions


def run(cfg, df):
    now = df.index[-1] + pd.Timedelta(days=1) if len(df) else pd.Timestamp("2025-01-01", tz="UTC")
    return check("X", "test", cfg, df, flag_move=0.25, max_gap_days=7, event="", now=now)


def test_flags_gaps_zero_volume_bad_ohlc_and_big_moves(settings):
    df = daily_bars(1200)  # ~3.3 years, above the 3-year crypto minimum
    df = df.drop(df.index[100])
    df.iloc[200, df.columns.get_loc("volume")] = 0
    df.iloc[300, df.columns.get_loc("high")] = df["low"].iloc[300] - 5
    df.iloc[400:, :4] *= 1.5  # one +50% jump, OHLC stays consistent
    row, details = run(settings.asset_classes["crypto"], df)
    assert (row["missing_days"], row["zero_volume"], row["ohlc_violations"], row["big_moves"]) == (1, 1, 1, 1)
    assert row["unexpected_bars"] == 0 and row["verdict"] == "WARN"
    assert any("missing" in d for d in details) and any("moves > 25%" in d for d in details)


def test_a_long_hole_restarts_usable_history_but_a_short_one_does_not(settings):
    df = daily_bars(1500)  # ~4.1 years
    short_hole = df.drop(df.index[700:702])  # 2 missing days: still one usable run
    row, _ = run(settings.asset_classes["crypto"], short_hole)
    assert row["verdict"] == "WARN" and row["usable_years"] == row["years_available"]
    long_hole = df.drop(df.index[500:900])  # 400 days without bars, like SOL/USD on Alpaca in 2023-24
    row, details = run(settings.asset_classes["crypto"], long_hole)
    assert row["verdict"] == "DROP" and row["usable_years"] < 3 and row["longest_gap_days"] == 401
    assert any("usable history restarts" in d for d in details)


def test_float_noise_in_adjusted_prices_is_not_an_ohlc_violation(settings):
    df = daily_bars(1200)
    df.iloc[10, df.columns.get_loc("high")] = df["close"].iloc[10] * (1 - 1e-9)
    row, _ = run(settings.asset_classes["crypto"], df)
    assert row["ohlc_violations"] == 0


def test_short_history_is_dropped(settings):
    row, _ = run(settings.asset_classes["crypto"], daily_bars(300))
    assert row["verdict"] == "DROP" and "minimum" in row["notes"]


def test_empty_history_is_dropped(settings):
    row, _ = run(settings.asset_classes["crypto"], daily_bars(0))
    assert row["verdict"] == "DROP"


def test_tsx_bars_are_checked_against_the_tsx_calendar(settings):
    cfg = settings.asset_classes["tsx"]
    stamps = close_timestamps(tsx_sessions("2024-01-02", "2024-12-31"), cfg.session.close, cfg.session.tz)
    df = daily_bars(len(stamps)).set_axis(stamps)
    row, _ = run(cfg, df)
    assert (row["missing_days"], row["unexpected_bars"]) == (0, 0)
    holiday = close_timestamps(pd.DatetimeIndex(["2024-07-01"]), cfg.session.close, cfg.session.tz)
    row, _ = run(cfg, pd.concat([df, df.iloc[:1].set_axis(holiday)]).sort_index())
    assert row["unexpected_bars"] == 1  # a bar on Canada Day
