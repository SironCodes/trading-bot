import numpy as np
import pandas as pd
import pytest

from bot.config import load_class
from bot.domain import Position
from bot.fusion.fusion import SignalFusion


def series(values, start="2020-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="D", tz="UTC", name="ts")
    close = pd.Series(values, index=idx, dtype=float)
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1e6})


@pytest.fixture
def strategy(settings):
    cfg = settings.asset_classes["tsx"].strategies[0]
    return load_class(cfg.class_path)(cfg)


def test_sma_trend_signals(strategy):
    up = series(np.linspace(50, 150, 300))
    down_after_up = series(np.r_[np.linspace(50, 150, 280), np.linspace(149, 120, 20)])
    choppy = series(np.r_[np.linspace(150, 100, 250), np.linspace(100, 106, 50)])  # close > SMA50 < SMA200
    short = series(np.linspace(50, 60, 100))
    sig = strategy.generate_signals({"UP": up, "DN": down_after_up, "CH": choppy, "NEW": short}, up.index[-1])
    assert 0 < sig["UP"] <= 1  # entry; magnitude = 63-day return capped at 1
    assert sig["DN"] == -1.0  # close below SMA50: exit
    assert sig["CH"] == 0.0  # no entry, no exit
    assert "NEW" not in sig  # not enough history: no signal at all
    assert "SMA50" in strategy.notes["UP"]


@pytest.fixture
def fusion(settings):
    return SignalFusion(settings.fusion)


def test_sentiment_alone_never_opens_a_trade(fusion):
    for signal in (None, 0.0, -1.0):
        assert fusion.fuse(signal, "", (0.95, 40), None, 100).action == "FLAT"


def test_negative_sentiment_vetoes_entries_only_with_enough_articles(fusion):
    assert fusion.fuse(0.2, "", (-0.6, 5), None, 100).action == "VETO"
    assert fusion.fuse(0.2, "", (-0.6, 2), None, 100).action == "ENTER"  # low coverage = neutral
    held = Position(10, 90, stop=80)
    assert fusion.fuse(0.2, "", (-0.9, 50), held, 100).action == "HOLD"  # sentiment never closes a position


@pytest.mark.parametrize("score,articles,expected", [
    (None, 0, 1.0), (0.0, 10, 1.0), (-0.2, 10, 1.0), (0.25, 10, 1.125), (0.5, 10, 1.25), (0.9, 10, 1.25),
    (0.9, 2, 1.0),
])
def test_positive_sentiment_scales_entry_size_up_to_the_cap(fusion, score, articles, expected):
    assert fusion.multiplier(score, articles) == pytest.approx(expected)


def test_held_positions_exit_on_stop_or_trend_break(fusion):
    held = Position(10, 100, stop=95)
    assert fusion.fuse(0.3, "", None, held, 94).action == "EXIT"  # stop checked on the close
    assert "stop" in fusion.fuse(0.3, "", None, held, 94).reason
    assert fusion.fuse(-1.0, "close < SMA50", None, held, 99).action == "EXIT"
    assert fusion.fuse(0.0, "", None, held, 99).action == "HOLD"
