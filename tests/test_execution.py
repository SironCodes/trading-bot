import json
import logging
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from bot.domain import OrderIntent
from bot.execution.brokers import alpaca as alpaca_mod
from bot.execution.brokers.base import Account, Ambiguous, Broker, BrokerOrder, RateLimited, Rejected
from bot.execution.brokers.canadian import CanadianBroker
from bot.execution.brokers.simulated import SimulatedBroker
from bot.execution.manager import CycleAborted, OrderManager
from bot.execution.router import BrokerRouter
from bot.market.calendar import close_timestamps, tsx_sessions
from bot.risk.limits import AccountState
from bot.storage import log as logsetup
from bot.storage.db import connect, halted, set_halted

SLOT = pd.Timestamp("2026-10-09 21:30", tz="UTC")
NOW = SLOT + pd.Timedelta(minutes=5)


class FakeBroker(Broker):
    def __init__(self, script=(), positions=None, fail=None):
        self.script, self.positions, self.fail = list(script), positions or {}, fail
        self.orders, self.placed, self.cancelled = {}, [], 0

    def get_account(self):
        return Account(100_000, 100_000, "CAD")

    def get_positions(self):
        return dict(self.positions)

    def get_open_orders(self):
        return [o for o in self.orders.values() if o.status == "accepted"]

    def get_order(self, coid):
        return self.orders.get(coid)

    def place_order(self, intent, coid):
        self.placed.append(coid)
        step = self.script.pop(0) if self.script else "accept"
        if step == "created-then-timeout":  # the request reached the broker but the reply was lost
            self.orders[coid] = BrokerOrder(coid, intent.symbol, intent.side, intent.qty, "accepted")
            raise Ambiguous("read timeout")
        if isinstance(step, Exception):
            raise step
        filled = intent.qty / 2 if step == "partial" else 0.0
        order = BrokerOrder(coid, intent.symbol, intent.side, intent.qty, "partially_filled" if filled else "accepted",
                            filled, 10.0 if filled else None, "b-1", [(filled, 10.0, 0.5, NOW)] if filled else [])
        self.orders[coid] = order
        return order

    def cancel_order(self, coid):
        self.orders[coid].status = "canceled"

    def cancel_all(self):
        if self.fail:
            raise self.fail
        n = len(self.get_open_orders())
        self.cancelled += n
        return n

    def flatten_all(self, prefix):
        return [BrokerOrder(f"{prefix}-{s}", s, "sell", q, "accepted") for s, (q, _) in self.positions.items()]


def intent(side="buy", qty=50.0, sym="RY.TO"):
    return OrderIntent("tsx", sym, side, qty, "limit", 101.0 if side == "buy" else 99.0, "day", f"test {side}",
                       95.0 if side == "buy" else None)


def state(**kw):
    base = dict(equity=100_000, prev_equity=100_000, cash=100_000, gross=0.0, held={"TD.TO": 100}, open_orders=0,
                combined_open_orders=0, combined_equity_cad=100_000, combined_gross_cad=0.0, cad_per_unit=1.0,
                regime_cap=1.0)
    return AccountState(**(base | kw))


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "state.sqlite")


def manager(settings, conn, broker, dry_run=False):
    return OrderManager("paper", BrokerRouter({"tsx": broker}), conn, settings, dry_run=dry_run, sleep=lambda s: None)


def orders(conn):
    return {r["client_order_id"]: dict(r) for r in conn.execute("SELECT * FROM orders")}


def test_client_order_ids_are_deterministic_per_slot_symbol_and_side(settings, conn):
    om = manager(settings, conn, FakeBroker())
    assert om.client_order_id("tsx", SLOT, "RY.TO", "buy") == "paper-tsx-20261009-RYTO-buy"
    assert om.client_order_id("crypto", SLOT, "BTC/USD", "sell") == "paper-crypto-20261009-BTCUSD-sell"
    assert manager(settings, conn, FakeBroker(), dry_run=True).client_order_id("tsx", SLOT, "RY.TO", "buy").startswith("dry-")


def test_router_dispatches_by_asset_class_and_reports_broken_brokers(settings, conn):
    tsx, broken = FakeBroker(), RuntimeError("no keys")
    router = BrokerRouter({"tsx": tsx, "crypto": broken})
    assert router.for_asset("tsx") is tsx and router.available() == {"tsx": tsx}
    with pytest.raises(RuntimeError, match="no keys"):
        router.for_asset("crypto")


def test_dry_run_records_intended_orders_and_sends_nothing(settings, conn):
    broker = FakeBroker()
    rows = manager(settings, conn, broker, dry_run=True).submit("c", "tsx", SLOT, [intent()], state(), {"RY.TO": 100}, NOW)
    assert broker.placed == [] and [r["status"] for r in rows] == ["dry_run"]
    rec = orders(conn)["dry-paper-tsx-20261009-RYTO-buy"]
    assert rec["reason"] == "test buy" and rec["time_in_force"] == "day" and rec["limit_price"] == 101.0


def test_rerunning_a_slot_never_duplicates_orders(settings, conn):
    broker = FakeBroker()
    om = manager(settings, conn, broker)
    for _ in range(3):
        om.submit("c", "tsx", SLOT, [intent(), intent("sell", 100, "TD.TO")], state(), {"RY.TO": 100, "TD.TO": 80}, NOW)
    assert sorted(broker.placed) == ["paper-tsx-20261009-RYTO-buy", "paper-tsx-20261009-TDTO-sell"]
    assert conn.execute("SELECT stop FROM stops WHERE symbol = 'RY.TO'").fetchone()["stop"] == 95.0


def test_risk_rejections_are_recorded_and_exits_still_go_out(settings, conn):
    broker = FakeBroker()
    manager(settings, conn, broker).submit("c", "tsx", SLOT, [intent(), intent("sell", 100, "TD.TO")],
                                           state(halted=True), {"RY.TO": 100, "TD.TO": 80}, NOW)
    rec = orders(conn)
    assert rec["paper-tsx-20261009-RYTO-buy"]["status"] == "rejected_by_risk"
    assert "kill switch" in rec["paper-tsx-20261009-RYTO-buy"]["error"]
    assert broker.placed == ["paper-tsx-20261009-TDTO-sell"]


def test_partial_fills_are_recorded(settings, conn):
    manager(settings, conn, FakeBroker(["partial"])).submit("c", "tsx", SLOT, [intent()], state(), {"RY.TO": 100}, NOW)
    rec = orders(conn)["paper-tsx-20261009-RYTO-buy"]
    assert (rec["status"], rec["filled_qty"], rec["avg_fill_price"]) == ("partially_filled", 25.0, 10.0)
    assert conn.execute("SELECT qty, price FROM fills").fetchone()[:] == (25.0, 10.0)


def test_rejections_are_never_retried(settings, conn):
    broker = FakeBroker([Rejected("HTTP 403: insufficient buying power")])
    manager(settings, conn, broker).submit("c", "tsx", SLOT, [intent()], state(), {"RY.TO": 100}, NOW)
    assert len(broker.placed) == 1 and orders(conn)["paper-tsx-20261009-RYTO-buy"]["status"] == "rejected"


def test_rate_limits_are_retried(settings, conn):
    broker = FakeBroker([RateLimited("429"), RateLimited("429")])
    manager(settings, conn, broker).submit("c", "tsx", SLOT, [intent()], state(), {"RY.TO": 100}, NOW)
    assert len(broker.placed) == 3 and orders(conn)["paper-tsx-20261009-RYTO-buy"]["status"] == "accepted"


def test_ambiguous_failure_is_looked_up_before_any_retry(settings, conn):
    broker = FakeBroker(["created-then-timeout"])
    manager(settings, conn, broker).submit("c", "tsx", SLOT, [intent()], state(), {"RY.TO": 100}, NOW)
    assert len(broker.placed) == 1  # the retry found the order instead of sending it again
    assert orders(conn)["paper-tsx-20261009-RYTO-buy"]["status"] == "accepted"


def test_repeated_api_errors_abort_the_cycle(settings, conn):
    broker = FakeBroker([Ambiguous("503")] * 3)
    with pytest.raises(CycleAborted):
        manager(settings, conn, broker).submit("c", "tsx", SLOT, [intent(), intent(sym="BMO.TO")], state(),
                                               {"RY.TO": 100, "BMO.TO": 100}, NOW)
    assert len(broker.placed) == 3 and orders(conn)["paper-tsx-20261009-RYTO-buy"]["status"] == "submitting"
    assert "paper-tsx-20261009-BMOTO-buy" not in orders(conn)  # nothing else was sent


def test_reconcile_marks_orders_that_never_reached_the_broker(settings, conn):
    broker = FakeBroker([Ambiguous("503")] * 3)
    om = manager(settings, conn, broker)
    with pytest.raises(CycleAborted):
        om.submit("c", "tsx", SLOT, [intent()], state(), {"RY.TO": 100}, NOW)
    om.reconcile(NOW)
    assert orders(conn)["paper-tsx-20261009-RYTO-buy"]["status"] == "not_submitted"


def test_kill_switch_cancels_flattens_latches_and_survives_a_broken_broker(settings, conn):
    tsx = FakeBroker(positions={"RY.TO": (50, 100.0)})
    tsx.orders["x"] = BrokerOrder("x", "TD.TO", "buy", 10, "accepted")
    crypto = FakeBroker(positions={"BTC/USD": (0.1, 50_000)}, fail=Ambiguous("down"))
    om = OrderManager("paper", BrokerRouter({"tsx": tsx, "crypto": crypto, "other": RuntimeError("no keys")}), conn,
                      settings, sleep=lambda s: None)
    report = om.kill(NOW)
    assert report["tsx"].startswith("cancelled 1 open orders; 1 market sell")
    assert report["crypto"].startswith("FAILED") and report["other"].startswith("FAILED")
    assert halted(conn)
    assert [r["reason"] for r in orders(conn).values()] == ["kill switch: flatten all"]
    entry = om.submit("c", "tsx", SLOT, [intent()], state(halted=halted(conn)), {"RY.TO": 100}, NOW)
    assert entry[0]["status"] == "rejected_by_risk"  # latched: no new entries
    set_halted(conn, False, NOW)
    assert not halted(conn)


def test_kill_switch_dry_run_sends_nothing(settings, conn):
    tsx = FakeBroker(positions={"RY.TO": (50, 100.0)})
    report = manager(settings, conn, tsx, dry_run=True).kill(NOW)
    assert report["tsx"].startswith("DRY RUN") and tsx.cancelled == 0 and not halted(conn) and orders(conn) == {}


def tsx_bars(settings, n=40):
    cfg = settings.asset_classes["tsx"]
    stamps = close_timestamps(tsx_sessions("2024-01-02", "2024-03-29")[:n], cfg.session.close, cfg.session.tz)
    close = np.linspace(100, 110, n)
    return pd.DataFrame({"open": close + 0.5, "high": close + 1, "low": close - 1, "close": close, "volume": 1e6},
                        index=pd.DatetimeIndex(stamps, name="ts"))


def test_simulated_broker_fills_at_the_next_open_with_the_backtest_model(settings, conn):
    df = tsx_bars(settings)
    sim = SimulatedBroker(settings, "tsx", "paper", conn, bars={"RY.TO": df})
    decided = df.index[10] + pd.Timedelta(minutes=90)  # 17:30 Toronto
    sim.now = decided
    sim.place_order(OrderIntent("tsx", "RY.TO", "buy", 50, "limit", 120.0, "day", "e"), "o1")
    assert sim.sync(decided) == [] and sim.get_order("o1").status == "accepted"  # next bar not closed yet
    [order] = sim.sync(df.index[11])
    expected_px = df["open"].iloc[11] * (1 + settings.asset_classes["tsx"].costs.slippage_bps / 1e4)
    assert order.status == "filled" and order.avg_price == pytest.approx(expected_px)
    assert sim.get_positions()["RY.TO"][0] == 50
    assert sim.get_account().cash == pytest.approx(100_000 - 50 * expected_px - 1.00)
    sim.now = df.index[12] + pd.Timedelta(minutes=90)
    sim.place_order(OrderIntent("tsx", "RY.TO", "sell", 50, "limit", 999.0, "day", "x"), "o2")  # not marketable
    [expired] = sim.sync(df.index[13])
    assert expired.status == "expired" and sim.get_positions()["RY.TO"][0] == 50
    [flat] = sim.flatten_all("kill")
    assert flat.status == "accepted" and sim.get_open_orders()[0].client_order_id == "kill-RYTO"
    sim.sync(df.index[14])
    assert sim.get_positions() == {}


class FakeTradingClient:
    calls = {}

    def __init__(self, key, secret, paper, url_override):
        FakeTradingClient.calls = {"paper": paper, "url": url_override}
        self.fail = None

    def get_all_positions(self):
        return [SimpleNamespace(symbol="BTCUSD", qty="0.5", avg_entry_price="60000")]

    def get_asset(self, symbol):
        return SimpleNamespace(min_trade_increment="0.0001", price_increment="1", min_order_size="0.0001")

    def submit_order(self, req):
        if self.fail:
            raise self.fail
        return SimpleNamespace(client_order_id=req.client_order_id, symbol=req.symbol, side=SimpleNamespace(value="buy"),
                               qty=str(req.qty), status=SimpleNamespace(value="new"), filled_qty="0",
                               filled_avg_price=None, id="abc", filled_at=None, limit=req.limit_price)

    def get_order_by_client_id(self, coid):
        raise api_error(404)


def api_error(code, msg="err"):
    from alpaca.common.exceptions import APIError
    return APIError(json.dumps({"code": code, "message": msg}),
                    http_error=SimpleNamespace(response=SimpleNamespace(status_code=code), request=None))


@pytest.fixture
def alpaca(settings, monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER_KEY", "fake-key-id")
    monkeypatch.setenv("ALPACA_PAPER_SECRET", "fake-secret")
    monkeypatch.setattr(alpaca_mod, "TradingClient", FakeTradingClient)
    return alpaca_mod.AlpacaBroker(settings, "crypto", "paper")


def test_alpaca_broker_uses_the_configured_endpoint_and_maps_symbols(alpaca):
    assert FakeTradingClient.calls == {"paper": True, "url": "https://paper-api.alpaca.markets"}
    assert alpaca.get_positions() == {"BTC/USD": (0.5, 60000.0)}
    order = alpaca.place_order(OrderIntent("crypto", "BTC/USD", "buy", 0.012345, "limit", 60123.7, "ioc", "e"), "c1")
    assert (order.status, order.qty, order.symbol) == ("accepted", 0.0123, "BTC/USD")


@pytest.mark.parametrize("error,kind", [(429, RateLimited), (500, Ambiguous), (403, Rejected),
                                        (ConnectionError("reset"), Ambiguous)])
def test_alpaca_errors_map_to_retry_policy(alpaca, error, kind):
    alpaca.client.fail = api_error(error) if isinstance(error, int) else error
    with pytest.raises(kind):
        alpaca.place_order(OrderIntent("crypto", "BTC/USD", "buy", 0.01, "limit", 60000, "ioc", "e"), "c2")


def test_canadian_broker_is_a_stub():
    with pytest.raises(NotImplementedError):
        CanadianBroker()


def test_secrets_never_reach_the_log(tmp_path):
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        logsetup.setup(tmp_path / "bot.jsonl", "INFO", {"ALPACA_PAPER_SECRET": "s3cr3t-value-123", "HOME": "/x"})
        logging.getLogger("t").info("connecting with s3cr3t-value-123", extra={"fields": {"k": "s3cr3t-value-123"}})
        text = (tmp_path / "bot.jsonl").read_text()
        assert "s3cr3t-value-123" not in text and "[REDACTED]" in text and json.loads(text.splitlines()[-1])
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])


def test_runner_dry_run_cycle_decides_and_sends_nothing(settings, conn, monkeypatch):
    from bot.runner import __main__ as runner
    cfg = settings.asset_classes["tsx"]
    stamps = close_timestamps(tsx_sessions("2025-01-02", "2026-10-09"), cfg.session.close, cfg.session.tz)
    rng = np.random.default_rng(0)
    bars = {}
    for i, sym in enumerate(["RY.TO", "TD.TO", "XIU.TO"]):
        close = 50 * np.exp(np.cumsum(0.001 * (i + 1) + rng.normal(0, 0.005, len(stamps))))
        bars[sym] = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                                  "volume": 1e7}, index=pd.DatetimeIndex(stamps, name="ts"))
    monkeypatch.setattr(runner, "load_bars", lambda s, name: (bars, {}))
    sim = SimulatedBroker(settings, "tsx", "paper", conn, bars=bars)
    router = BrokerRouter({"tsx": sim})
    om = OrderManager("paper", router, conn, settings, dry_run=True)
    result = runner.run_cycle(settings, "paper", conn, om, router, "tsx", NOW, True, None, None, {"XIU.TO": bars["XIU.TO"]})
    assert result.startswith("slot 2026-10-09 21:30Z")
    assert conn.execute("SELECT COUNT(*) FROM sim_orders").fetchone()[0] == 0  # nothing reached the broker
    statuses = {r["status"] for r in conn.execute("SELECT status FROM orders")}
    assert statuses == {"dry_run"}
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 3
    again = runner.run_cycle(settings, "paper", conn, om, router, "tsx", NOW, True, None, None, {"XIU.TO": bars["XIU.TO"]})
    assert "already done" in again
