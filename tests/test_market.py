import pandas as pd
import pytest

from bot.domain import OrderIntent
from bot.market.costs import fee, slipped
from bot.market.fills import fill
from bot.market.rules import round_price, round_qty


def order(side="buy", qty=100.0, limit=10.0, kind="limit"):
    return OrderIntent("tsx", "X", side, qty, kind, limit if kind == "limit" else None, "day", "test")


def bar(open_=10.0, volume=1e6):
    return pd.Series({"open": open_, "high": open_ * 1.02, "low": open_ * 0.98, "close": open_, "volume": volume})


@pytest.mark.parametrize("price,side,expected", [
    (12.347, "buy", 12.34), (12.341, "sell", 12.35), (0.4321, "buy", 0.43), (0.4321, "sell", 0.435),
    (12.35, "buy", 12.35), (12.35, "sell", 12.35),
])
def test_tsx_tick_rounding_never_loosens_a_limit(settings, price, side, expected):
    assert round_price(price, settings.asset_classes["tsx"].rules, side) == pytest.approx(expected)


def test_tsx_whole_shares_and_optional_board_lots(settings):
    rules = settings.asset_classes["tsx"].rules
    assert round_qty(57.9, 170.0, rules) == 57  # whole shares (odd lots allowed)
    lots = rules.model_copy(update={"lot_policy": "board_lots"})
    assert round_qty(257, 170.0, lots) == 200  # >= $1: 100-share lots
    assert round_qty(1700, 0.50, lots) == 1500  # $0.10-$0.99: 500-share lots
    assert round_qty(2500, 0.05, lots) == 2000  # < $0.10: 1000-share lots


def test_crypto_quantity_step(settings):
    assert round_qty(0.123456, 60000, settings.asset_classes["crypto"].rules) == pytest.approx(0.1234)


def test_commission_minimum_crypto_bps_and_cost_multiplier(settings):
    tsx, crypto = settings.asset_classes["tsx"].costs, settings.asset_classes["crypto"].costs
    assert fee(tsx, 50, 100) == 1.00  # 50 x 0.01 = 0.50 -> minimum 1.00
    assert fee(tsx, 500, 100) == pytest.approx(5.00)
    assert fee(crypto, 0.1, 50000) == pytest.approx(12.5)  # 25 bps taker on 5,000 USD
    assert fee(crypto, 0.1, 50000, mult=2) == pytest.approx(25.0)
    assert slipped(100, "buy", tsx) == pytest.approx(100.10) and slipped(100, "sell", tsx, 2) == pytest.approx(99.80)


def test_fill_at_next_open_plus_slippage(settings):
    qty, px, f = fill(order(limit=10.5), bar(10.0), settings.asset_classes["tsx"])
    assert (qty, round(px, 4), f) == (100, 10.01, 1.00)


def test_limit_not_marketable_at_open_does_not_fill(settings):
    assert fill(order("buy", limit=9.9), bar(10.0), settings.asset_classes["tsx"]) is None
    assert fill(order("sell", limit=10.1), bar(10.0), settings.asset_classes["tsx"]) is None


def test_fill_is_never_worse_than_the_limit(settings):
    qty, px, _ = fill(order("buy", limit=10.005), bar(10.0), settings.asset_classes["tsx"])
    assert px == 10.005  # slippage would give 10.01, the limit caps it


def test_volume_cap_makes_partial_fills_and_cash_caps_buys(settings):
    cfg = settings.asset_classes["tsx"]
    assert fill(order(qty=1000, limit=11), bar(10.0, volume=4000), cfg)[0] == 200  # 5% of 4,000
    qty, px, f = fill(order(qty=1000, limit=11), bar(10.0), cfg, cash=5000)
    assert qty * px + f <= 5000 and qty >= 498  # affordable; may leave one share of slack (conservative)


def test_market_order_fills_regardless_of_price(settings):
    assert fill(order("sell", kind="market"), bar(10.0), settings.asset_classes["tsx"])[1] == pytest.approx(9.99)
