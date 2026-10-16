"""AlpacaBroker: crypto through Alpaca, paper or live as chosen by the resolved environment's endpoint and
keys. The only module in the project allowed to import alpaca.trading."""
import math
import os

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, LimitOrderRequest, MarketOrderRequest

from bot.config import alpaca_credentials, alpaca_endpoint
from bot.execution.brokers.base import Account, Ambiguous, Broker, BrokerOrder, RateLimited, Rejected
from bot.market.costs import fee

EPS = 1e-9
STATUS = {"new": "accepted", "accepted": "accepted", "pending_new": "accepted", "accepted_for_bidding": "accepted",
          "partially_filled": "partially_filled", "filled": "filled", "done_for_day": "expired",
          "canceled": "canceled", "expired": "expired", "replaced": "canceled", "pending_cancel": "accepted",
          "pending_replace": "accepted", "rejected": "rejected", "suspended": "rejected", "stopped": "accepted",
          "calculated": "accepted"}


class AlpacaBroker(Broker):
    def __init__(self, settings, asset_class, env, conn=None):
        key, secret = alpaca_credentials(env, os.environ)
        self.client = TradingClient(key, secret.get_secret_value(), paper=env == "paper",
                                    url_override=alpaca_endpoint(settings, env))
        self.cfg, self.assets = settings.asset_classes[asset_class], {}
        self.symbols = {s.replace("/", ""): s for s in self.cfg.symbols}  # positions come back as "BTCUSD"

    def _sym(self, symbol: str) -> str:
        return self.symbols.get(symbol.replace("/", ""), symbol)

    def _call(self, fn, *args):
        try:
            return fn(*args)
        except APIError as e:
            code = e.status_code
            if code == 429:
                raise RateLimited(str(e)) from e
            if code is None or code >= 500:
                raise Ambiguous(f"HTTP {code}: {e}") from e
            raise Rejected(f"HTTP {code}: {e}") from e
        except Exception as e:  # network failure: the request may or may not have reached Alpaca
            raise Ambiguous(f"{type(e).__name__}: {e}") from e

    def _order(self, o) -> BrokerOrder:
        filled, avg = float(o.filled_qty or 0), float(o.filled_avg_price) if o.filled_avg_price else None
        # Alpaca posts crypto fees at end of day; record the configured-rate estimate for now.
        fills = [(filled, avg, fee(self.cfg.costs, filled, avg), o.filled_at)] if filled and avg else []
        return BrokerOrder(o.client_order_id, self._sym(o.symbol), o.side.value, float(o.qty or 0),
                           STATUS.get(o.status.value, o.status.value), filled, avg, str(o.id), fills)

    def get_account(self):
        a = self._call(self.client.get_account)
        return Account(float(a.equity), float(a.non_marginable_buying_power), "USD")

    def get_positions(self):
        return {self._sym(p.symbol): (float(p.qty), float(p.avg_entry_price))
                for p in self._call(self.client.get_all_positions) if self._sym(p.symbol) in self.cfg.symbols}

    def get_open_orders(self):
        orders = self._call(self.client.get_orders, GetOrdersRequest(status=QueryOrderStatus.OPEN))
        return [self._order(o) for o in orders if self._sym(o.symbol) in self.cfg.symbols]

    def get_order(self, client_order_id):
        try:
            return self._order(self._call(self.client.get_order_by_client_id, client_order_id))
        except Rejected as e:
            if "404" in str(e):
                return None
            raise

    def _asset(self, symbol):
        if symbol not in self.assets:
            self.assets[symbol] = self._call(self.client.get_asset, symbol)
        return self.assets[symbol]

    def place_order(self, intent, client_order_id):
        a = self._asset(intent.symbol)
        step, tick = float(a.min_trade_increment or 0), float(a.price_increment or 0)
        qty = round(math.floor(intent.qty / step + EPS) * step, 9) if step else intent.qty
        if a.min_order_size and qty < float(a.min_order_size):
            raise Rejected(f"qty {qty} below Alpaca's minimum order size {a.min_order_size}")
        common = {"symbol": intent.symbol, "qty": qty, "side": OrderSide(intent.side),
                  "time_in_force": TimeInForce(intent.time_in_force), "client_order_id": client_order_id}
        if intent.order_type == "market":
            req = MarketOrderRequest(**common)
        else:
            snap = math.floor if intent.side == "buy" else math.ceil  # never loosen the limit
            limit = round(snap(intent.limit_price / tick + (EPS if intent.side == "buy" else -EPS)) * tick, 9) \
                if tick else intent.limit_price
            req = LimitOrderRequest(**common, limit_price=limit)
        try:
            return self._order(self._call(self.client.submit_order, req))
        except Rejected as e:
            if "client_order_id" in str(e) and (existing := self.get_order(client_order_id)):
                return existing  # duplicate id: the order already exists, never place it twice
            raise

    def cancel_order(self, client_order_id):
        order = self.get_order(client_order_id)
        if order and order.broker_order_id:
            self._call(self.client.cancel_order_by_id, order.broker_order_id)

    def cancel_all(self):
        return len(self._call(self.client.cancel_orders) or [])

    def flatten_all(self, id_prefix):
        responses = self._call(self.client.close_all_positions, True) or []  # market orders, cancels open ones
        return [BrokerOrder(f"{id_prefix}-{r.symbol}", self._sym(r.symbol), "sell", 0.0,
                            "accepted" if 200 <= int(r.status) < 300 else "rejected",
                            broker_order_id=str(r.order_id) if r.order_id else None) for r in responses]
