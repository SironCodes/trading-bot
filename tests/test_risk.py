import pandas as pd
import pytest

from bot.domain import OrderIntent
from bot.risk.limits import AccountState, check_order
from bot.risk.sizing import atr, max_position_value, risk_qty, stop_price


def test_volatility_scaled_size_and_stop():
    assert risk_qty(100_000, 0.01, 2.0, 3.0) == pytest.approx(1000 / 6)  # lose 1% if 3 ATR away
    assert stop_price(100, 2.0, 3.0) == 94


def test_atr_is_mean_true_range_including_gaps():
    df = pd.DataFrame({"high": [10, 11, 12], "low": [9, 10, 11], "close": [9.5, 10.5, 11.5]})
    assert atr(df, 2) == pytest.approx(1.5)  # TRs: max(1, |11-9.5|) = 1.5 and max(1, |12-10.5|) = 1.5


def test_max_position_pct_or_cad_tighter_wins(settings):
    mp = settings.asset_classes["tsx"].risk.max_position
    assert max_position_value(100_000, mp, 1.0) == 10_000
    both = mp.model_copy(update={"cad": 5_000})
    assert max_position_value(100_000, both, 1.0) == 5_000
    assert max_position_value(100_000, both, 1.4) == pytest.approx(5_000 / 1.4)  # CAD cap in USD terms


def buy(qty=50, price=100.0, sym="X"):
    return OrderIntent("tsx", sym, "buy", qty, "limit", price, "day", "t")


def sell(qty=50, sym="X"):
    return OrderIntent("tsx", sym, "sell", qty, "limit", 99.0, "day", "t")


def state(**kw):
    base = dict(equity=100_000, prev_equity=100_000, cash=100_000, gross=0.0, held={}, open_orders=0,
                combined_open_orders=0, combined_equity_cad=100_000, combined_gross_cad=0.0, cad_per_unit=1.0,
                regime_cap=1.0)
    return AccountState(**(base | kw))


@pytest.fixture
def risk(settings):
    return settings.asset_classes["tsx"].risk


def test_clean_order_passes(settings, risk):
    assert check_order(buy(), 100, state(), risk, settings.combined_risk) == (True, "ok")


@pytest.mark.parametrize("overrides,intent,needle", [
    ({"halted": True}, buy(), "kill switch"),
    ({"equity": 96_000, "prev_equity": 100_000}, buy(), "daily loss"),
    ({"open_orders": 20}, buy(), "open orders"),
    ({"combined_open_orders": 25}, buy(), "across asset classes"),
    ({}, buy(qty=101), "position would be"),  # 10,100 > 10% of 100k
    ({"gross": 59_500, "regime_cap": 0.60}, buy(), "gross exposure"),
    ({"combined_gross_cad": 95_000}, buy(), "combined gross"),
    ({}, buy(qty=4), "minimum"),
    ({"cash": 1_000}, buy(), "cash"),
    ({}, sell(), "long-only"),
])
def test_limits_reject(settings, risk, overrides, intent, needle):
    ok, why = check_order(intent, 100, state(**overrides), risk, settings.combined_risk)
    assert not ok and needle in why


def test_exits_stay_allowed_when_entries_are_blocked(settings, risk):
    blocked = state(halted=True, equity=90_000, prev_equity=100_000, held={"X": 50}, open_orders=99)
    assert check_order(sell(), 100, blocked, risk, settings.combined_risk)[0]


def test_orders_in_the_same_cycle_count_against_limits(settings, risk):
    s = state(regime_cap=0.6)
    for i in range(6):  # six 10k positions fill the 60k gross cap (regime 60% < max_gross 95%)
        assert check_order(buy(qty=100, sym=f"S{i}"), 100, s, risk, settings.combined_risk)[0]
        s = s.after(buy(qty=100, sym=f"S{i}"), 100)
    ok, why = check_order(buy(qty=100, sym="S9"), 100, s, risk, settings.combined_risk)
    assert not ok and "gross exposure" in why
