"""SimulatedBroker: the TSX paper broker. A local SQLite ledger (the source of truth) that fills with the
backtester's own fill and cost model: an order waits for the first bar that opens after it was placed and
fills at that bar's open once the bar has closed. A DAY order that is not marketable at the open, or the
unfilled rest of a partial fill, expires."""
import pandas as pd

from bot.data.universe import load_bars
from bot.domain import Book, OrderIntent
from bot.execution.brokers.base import Account, Broker, BrokerOrder
from bot.market.fills import fill
from bot.storage.db import insert


class SimulatedBroker(Broker):
    realtime = False

    def __init__(self, settings, asset_class, env, conn, bars=None):
        self.cfg, self.conn, self.now = settings.asset_classes[asset_class], conn, pd.Timestamp.now(tz="UTC")
        self.bars = bars if bars is not None else load_bars(settings, asset_class)[0]
        if conn.execute("SELECT COUNT(*) FROM sim_cash").fetchone()[0] == 0:
            insert(conn, "sim_cash", {"amount": self.cfg.capital_cad, "ts": self.now, "note": "opening cash"})

    def _book(self) -> Book:
        book = Book(self.conn.execute("SELECT SUM(amount) FROM sim_cash").fetchone()[0])
        for f in self.conn.execute("SELECT * FROM sim_fills ORDER BY rowid"):
            book.apply(f["symbol"], f["side"], f["qty"], f["price"], f["fee"])
        return book

    def _last_close(self, symbol) -> float:
        df = self.bars[symbol]
        return float(df["close"].iloc[df.index.searchsorted(self.now, side="right") - 1])

    def _order(self, row, fills=()) -> BrokerOrder:
        return BrokerOrder(row["client_order_id"], row["symbol"], row["side"], row["qty"], row["status"],
                           row["filled_qty"] or 0.0, row["avg_price"], row["client_order_id"], list(fills))

    def get_account(self):
        book = self._book()
        return Account(book.equity({s: self._last_close(s) for s in book.positions}), book.cash, self.cfg.currency)

    def get_positions(self):
        return {s: (p.qty, p.avg_price) for s, p in self._book().positions.items()}

    def get_open_orders(self):
        return [self._order(r) for r in self.conn.execute("SELECT * FROM sim_orders WHERE status = 'accepted'")]

    def get_order(self, client_order_id):
        row = self.conn.execute("SELECT * FROM sim_orders WHERE client_order_id = ?", (client_order_id,)).fetchone()
        return self._order(row) if row else None

    def place_order(self, intent, client_order_id):
        if existing := self.get_order(client_order_id):
            return existing
        insert(self.conn, "sim_orders", {
            "client_order_id": client_order_id, "symbol": intent.symbol, "side": intent.side, "qty": intent.qty,
            "order_type": intent.order_type, "limit_price": intent.limit_price,
            "time_in_force": intent.time_in_force, "submitted_at": self.now, "status": "accepted"})
        return self.get_order(client_order_id)

    def cancel_order(self, client_order_id):
        self.conn.execute("UPDATE sim_orders SET status = 'canceled' WHERE client_order_id = ? AND status = 'accepted'",
                          (client_order_id,))

    def cancel_all(self):
        return self.conn.execute("UPDATE sim_orders SET status = 'canceled' WHERE status = 'accepted'").rowcount

    def flatten_all(self, id_prefix):
        self.cancel_all()
        return [self.place_order(OrderIntent("", sym, "sell", qty, "market", None, "day", "kill switch"),
                                 f"{id_prefix}-{''.join(c for c in sym if c.isalnum())}")
                for sym, (qty, _) in self.get_positions().items()]

    def sync(self, now):
        """Fill or expire every waiting order whose next bar has closed by `now`."""
        self.now, changed = now, []
        session = self.cfg.session
        for r in self.conn.execute("SELECT * FROM sim_orders WHERE status = 'accepted' ORDER BY rowid").fetchall():
            df = self.bars.get(r["symbol"])
            i = df.index.searchsorted(pd.Timestamp(r["submitted_at"]), side="right") if df is not None else 0
            if df is None or i >= len(df) or df.index[i] > now:
                continue  # the next bar has not closed yet: keep waiting
            book = self._book()
            intent = OrderIntent("", r["symbol"], r["side"], r["qty"], r["order_type"], r["limit_price"],
                                 r["time_in_force"], "")
            res = fill(intent, df.iloc[i], self.cfg, 1.0, book.cash if r["side"] == "buy" else None)
            if res and r["side"] == "sell":
                res = (min(res[0], book.positions[r["symbol"]].qty), *res[1:]) if r["symbol"] in book.positions else None
            fills = []
            if res:
                qty, px, fee = res
                ts = df.index[i] - (pd.Timedelta(hours=session.close.hour, minutes=session.close.minute)
                                    - pd.Timedelta(hours=session.open.hour, minutes=session.open.minute))  # the open
                insert(self.conn, "sim_fills", {"client_order_id": r["client_order_id"], "symbol": r["symbol"],
                                                "side": r["side"], "qty": qty, "price": px, "fee": fee, "ts": ts})
                fills = [(qty, px, fee, ts)]
                status = "filled" if qty >= r["qty"] else "expired"  # a DAY order's unfilled rest expires
                self.conn.execute("UPDATE sim_orders SET status = ?, filled_qty = ?, avg_price = ? "
                                  "WHERE client_order_id = ?", (status, qty, px, r["client_order_id"]))
            else:
                self.conn.execute("UPDATE sim_orders SET status = 'expired' WHERE client_order_id = ?",
                                  (r["client_order_id"],))
            changed.append(self._order(self.conn.execute("SELECT * FROM sim_orders WHERE client_order_id = ?",
                                                         (r["client_order_id"],)).fetchone(), fills))
        return changed
