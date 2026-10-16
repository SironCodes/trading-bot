import numpy as np
import pandas as pd
import pytest

from bot.domain import Book, Position
from bot.pipeline import decide, make_context
from bot.regime.macro import RegimeState

ON = RegimeState("RISK_ON", 1.0, 2, {}, "RISK_ON")
OFF = RegimeState("RISK_OFF", 0.25, -3, {}, "RISK_OFF")


def trending(n=300, slope=0.003, start=100.0, seed=0, noise=0.001):
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(slope + rng.normal(0, noise, n)))
    idx = pd.date_range("2024-01-01 21:00", periods=n, freq="D", tz="UTC", name="ts")
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                         "volume": 1e7}, index=idx)


@pytest.fixture
def ctx(settings):
    return make_context("tsx", settings.asset_classes["tsx"], settings.fusion)


def test_entries_are_ranked_capped_and_explained(ctx):
    bars = {f"S{i:02d}": trending(slope=0.001 + 0.0005 * i, seed=i) for i in range(14)}
    as_of = bars["S00"].index[-1]
    decisions, intents = decide(ctx, as_of, bars, Book(100_000), ON, None, 1.0)
    buys = [i for i in intents if i.side == "buy"]
    assert len(buys) == 10  # max_positions
    assert {b.symbol for b in buys} == {f"S{i:02d}" for i in range(4, 14)}  # strongest momentum first
    assert all(b.limit_price > 0 and b.stop < b.limit_price and "binds" in b.reason for b in buys)
    assert sum(b.qty * bars[b.symbol]["close"].iloc[-1] for b in buys) <= 95_000  # gross cap
    skipped = [d for d in decisions if d.action == "SKIP"]
    assert len(skipped) == 4 and all("max positions" in d.reason for d in skipped)
    assert len(decisions) == 14 and all(d.reason for d in decisions)


def test_regime_drop_trims_held_positions(ctx):
    bars = {f"S{i}": trending(seed=i) for i in range(8)}
    as_of = bars["S0"].index[-1]
    px = {s: df["close"].iloc[-1] for s, df in bars.items()}
    book = Book(22_000, {s: Position(round(6_000 / px[s]), px[s] * 0.8, stop=1.0) for s in bars})
    equity = book.equity(px)
    decisions, intents = decide(ctx, as_of, bars, book, OFF, None, 1.0)
    trims = [i for i in intents if i.side == "sell"]
    assert len(trims) == 8 and all("TRIM" in i.reason and "RISK_OFF" in i.reason for i in trims)
    kept = sum((book.positions[i.symbol].qty - i.qty) * px[i.symbol] for i in trims)
    assert kept <= 0.25 * equity * 1.01  # back within the 25% regime cap (whole-share rounding aside)
    assert not [i for i in intents if i.side == "buy"]


def test_exit_sells_the_whole_position(ctx):
    df = trending()
    df.iloc[-1, :4] = df["close"].iloc[-60:].mean() * 0.9  # close drops below SMA50
    book = Book(90_000, {"A": Position(100, 80.0, stop=1.0)})
    decisions, intents = decide(ctx, df.index[-1], {"A": df}, book, ON, None, 1.0)
    assert [(i.side, i.qty) for i in intents] == [("sell", 100)] and decisions[0].action == "EXIT"


def test_decisions_ignore_bars_after_as_of(ctx):
    full = {"A": trending(seed=3), "B": trending(seed=4, slope=-0.002)}
    as_of = full["A"].index[250]
    cut = {s: df[df.index <= as_of] for s, df in full.items()}
    future = {s: df.copy() for s, df in full.items()}
    for df in future.values():
        df.iloc[251:, :4] *= 3  # wildly different future
    for bars in (cut, future):
        d, i = decide(ctx, as_of, bars, Book(100_000), ON, None, 1.0)
        assert [(x.symbol, x.qty, x.limit_price) for x in i] == [(x.symbol, x.qty, x.limit_price) for x in
                                                                 decide(ctx, as_of, cut, Book(100_000), ON, None, 1.0)[1]]
