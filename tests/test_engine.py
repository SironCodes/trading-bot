import numpy as np
import pandas as pd
import pytest

from bot.backtest import engine
from bot.backtest.metrics import summarize
from bot.backtest.walkforward import folds, grid
from bot.data.fx import rate_at
from bot.market.calendar import close_timestamps, tsx_sessions
from bot.sentiment.aggregate import NewsIndex


def tsx_bars(settings, seed, slope=0.002, start="2022-01-03", end="2024-06-28"):
    cfg = settings.asset_classes["tsx"]
    stamps = close_timestamps(tsx_sessions(start, end), cfg.session.close, cfg.session.tz)
    rng = np.random.default_rng(seed)
    close = 50 * np.exp(np.cumsum(slope + rng.normal(0, 0.01, len(stamps))))
    open_ = close * (1 + rng.normal(0, 0.003, len(stamps)))
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * 1.005,
                         "low": np.minimum(open_, close) * 0.995, "close": close, "volume": 1e7},
                        index=pd.DatetimeIndex(stamps, name="ts"))


@pytest.fixture
def sleeve(settings):
    bars = {f"S{i}": tsx_bars(settings, i) for i in range(4)}
    return engine.Sleeve("tsx", settings.asset_classes["tsx"], bars)


def test_orders_fill_at_the_open_of_the_next_session(settings, sleeve):
    res = engine.run(settings, [sleeve], "2023-01-03", "2024-06-28")["tsx"]
    filled = res.orders[res.orders["status"] == "filled"]
    assert len(filled) > 0
    for row in filled.itertuples():
        df = sleeve.bars[row.symbol]
        nxt = df.index[df.index.searchsorted(row.decided_at, side="right")]
        assert row.fill_bar_close == nxt  # never the decision day's own bar
        open_ = df.loc[nxt, "open"]
        slip = settings.asset_classes["tsx"].costs.slippage_bps / 1e4
        expected = min(open_ * (1 + slip), row.limit_price) if row.side == "buy" else max(open_ * (1 - slip), row.limit_price)
        assert row.fill_price == pytest.approx(expected)


def test_backtest_never_looks_ahead(settings, sleeve):
    """Results up to T are identical whether or not (wildly different) bars after T exist."""
    full = engine.run(settings, [sleeve], "2023-01-03", "2024-06-28")["tsx"]
    cut_at = pd.Timestamp("2023-12-29 22:31", tz="UTC")  # after the 2023-12-29 decision
    future = engine.Sleeve("tsx", sleeve.cfg, {s: df.copy() for s, df in sleeve.bars.items()})
    for df in future.bars.values():
        df.loc[df.index > cut_at, ["open", "high", "low", "close"]] *= 0.3  # a crash only the future knows
    other = engine.run(settings, [future], "2023-01-03", "2024-06-28")["tsx"]
    a, b = full.equity[full.equity.index <= cut_at], other.equity[other.equity.index <= cut_at]
    assert len(a) > 200 and a.equals(b)


def test_crypto_equity_is_converted_to_cad_with_the_prior_days_rate(settings):
    cfg = settings.asset_classes["crypto"]
    idx = pd.date_range("2023-01-02", "2024-03-01", freq="D", tz="UTC", name="ts")
    close = pd.Series(np.linspace(20_000, 40_000, len(idx)), index=idx)
    bars = {"BTC/USD": pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                                     "volume": 1e6})}
    days = pd.bdate_range("2022-12-01", "2024-03-01")
    fx = pd.Series(np.linspace(1.30, 1.40, len(days)), index=days)
    res = engine.run(settings, [engine.Sleeve("crypto", cfg, bars)], "2023-09-01", "2024-02-01", fx=fx)["crypto"]
    t = res.equity.index[10]
    assert res.equity_cad[t] == pytest.approx(res.equity[t] * rate_at(fx, t))
    day = t.tz_convert("America/Toronto").tz_localize(None).normalize()
    assert fx.index[fx.index.searchsorted(day) - 1] < day  # the rate used was published before that day


def test_news_counts_only_if_available_strictly_before_the_decision():
    t = pd.Timestamp("2024-03-01 21:30", tz="UTC")
    docs = pd.DataFrame({"symbol": ["X", "X", "X"], "available_at": [t - pd.Timedelta(hours=1), t, t + pd.Timedelta(seconds=1)],
                         "headline": ["Bank beats estimates", "Bank cuts jobs", "Bank fined"],
                         "score": [0.8, -0.9, -0.9], "confidence": [1.0, 1.0, 1.0]})
    score, n = NewsIndex(docs, 0.8).score("X", t, lookback_hours=72, half_life_hours=24)
    assert (score, n) == (pytest.approx(0.8), 1)


def test_old_news_first_seen_today_is_visible_but_not_fresh():
    """An RSS article published weeks ago and first archived today must not count as recent news."""
    t = pd.Timestamp("2026-10-12 21:30", tz="UTC")
    seen = t - pd.Timedelta(hours=1)
    docs = pd.DataFrame({"symbol": ["X", "X"], "published_at": [t - pd.Timedelta(days=20), t - pd.Timedelta(hours=30)],
                         "available_at": [seen, seen], "headline": ["Old story", "Recent story"],
                         "score": [-0.9, 0.6], "confidence": [1.0, 1.0]})
    news = NewsIndex(docs, 0.8)
    assert news.score("X", t, 72, 24) == (pytest.approx(0.6), 1)  # only the recent one, aged from publication
    assert news.score("X", seen, 72, 24) == (None, 0)  # nothing was visible before the bot first saw it


def test_near_duplicate_headlines_are_counted_once_and_old_news_decays():
    t = pd.Timestamp("2024-03-01", tz="UTC")
    docs = pd.DataFrame({"symbol": ["X"] * 3,
                         "available_at": [t - pd.Timedelta(hours=48), t - pd.Timedelta(hours=2), t - pd.Timedelta(hours=1)],
                         "headline": ["RBC profit rises - Reuters", "RBC profit rises on strong lending - Globe",
                                      "RBC profit rises - Bloomberg"],
                         "score": [-1.0, 1.0, 1.0], "confidence": [1.0, 1.0, 1.0]})
    score, n = NewsIndex(docs, 0.8).score("X", t, 72, 24)
    assert n == 2  # the Bloomberg copy duplicates the Reuters headline
    assert score == pytest.approx((-0.25 + 2 ** (-2 / 24)) / (0.25 + 2 ** (-2 / 24)))


def test_metrics_on_known_curves():
    idx = pd.date_range("2020-01-01", periods=731, freq="D")
    doubling = pd.Series(100 * 2 ** (np.arange(731) / 730), index=idx)
    m = summarize(doubling, 365)
    assert m["total_return"] == pytest.approx(1.0) and m["cagr"] == pytest.approx(2 ** (365.25 / 730) - 1, rel=1e-3)
    assert m["max_drawdown"] == 0
    dd = summarize(pd.Series([100, 120, 90, 110], index=idx[:4]), 365)
    assert dd["max_drawdown"] == pytest.approx(-0.25)


def test_walk_forward_windows_and_grid(settings):
    f = folds("2021-10-12", "2026-10-09", settings.asset_classes["tsx"].walk_forward)
    assert len(f) == 6 and f[0][1] == pd.Timestamp("2023-10-12") and f[-1][2] == pd.Timestamp("2026-10-09")
    assert all(a[1] == b[1] - pd.DateOffset(months=6) for a, b in zip(f, f[1:]))  # rolling by the OOS length
    g = grid(settings.asset_classes["tsx"])
    assert len(g) == 8 and g[0] == {"fast": 50, "slow": 200, "momentum": 63}
