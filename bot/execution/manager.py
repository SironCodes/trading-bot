"""OrderManager: the ONE code path that places, cancels or flattens orders.

Before every order: a deterministic client_order_id (checked locally and at the broker, so reruns never
duplicate), risk.limits.check_order, then --dry-run logging or submission with a safe retry policy.
Every order is recorded in SQLite and the structured log with the reason it was placed.
"""
import logging
import time

from bot.execution.brokers.base import Ambiguous, RateLimited, Rejected
from bot.risk.limits import check_order
from bot.storage.db import TERMINAL, insert, set_halted

log = logging.getLogger("bot.execution")
ATTEMPTS = 3  # project rule: stop after the same API error 3 times
BACKOFF_S = 2.0


class CycleAborted(RuntimeError):
    pass


class OrderManager:
    def __init__(self, env, router, conn, settings, dry_run=False, sleep=time.sleep):
        self.env, self.router, self.conn, self.settings, self.dry_run, self.sleep = env, router, conn, settings, dry_run, sleep

    def client_order_id(self, asset_class, slot, symbol, side) -> str:
        """Deterministic per environment, decision slot, symbol and side: a rerun reuses it."""
        clean = "".join(c for c in symbol if c.isalnum())
        return f"{'dry-' if self.dry_run else ''}{self.env}-{asset_class}-{slot:%Y%m%d}-{clean}-{side}"

    def _record(self, row: dict) -> None:
        insert(self.conn, "orders", row, replace=True)
        log.info("order %s: %s %s %g %s @ %s | %s", row["status"], row["side"], row["symbol"], row["qty"],
                 row["order_type"], row["limit_price"], row["reason"], extra={"fields": {"order": row}})

    def _apply(self, row: dict, order, now) -> None:
        self._record(row | {"status": order.status, "filled_qty": order.filled_qty, "avg_fill_price": order.avg_price,
                            "broker_order_id": order.broker_order_id, "updated_at": now})
        for qty, px, fee, ts in order.fills:
            self.conn.execute("INSERT OR IGNORE INTO fills VALUES (?, ?, ?, ?, ?, ?, ?)",
                              (order.client_order_id, order.symbol, order.side, qty, px, fee, str(ts)))

    def submit(self, cycle_id, asset_class, slot, intents, state, prices, now) -> list[dict]:
        """Risk-check, record and (unless dry-run) send each intent. Returns the order rows written."""
        broker, risk = self.router.for_asset(asset_class), self.settings.asset_classes[asset_class].risk
        rows = []
        for it in intents:
            coid = self.client_order_id(asset_class, slot, it.symbol, it.side)
            if self.conn.execute("SELECT 1 FROM orders WHERE client_order_id = ?", (coid,)).fetchone():
                log.info("skip %s: already handled for this slot", coid)
                continue
            row = {"client_order_id": coid, "cycle_id": cycle_id, "env": self.env, "broker": type(broker).__name__,
                   "asset_class": asset_class, "symbol": it.symbol, "side": it.side, "qty": it.qty,
                   "order_type": it.order_type, "limit_price": it.limit_price, "time_in_force": it.time_in_force,
                   "reason": it.reason, "created_at": now, "updated_at": now, "dry_run": int(self.dry_run),
                   "status": "", "error": None}
            ok, why = check_order(it, prices[it.symbol], state, risk, self.settings.combined_risk)
            if not ok:
                self._record(row | {"status": "rejected_by_risk", "error": why})
                rows.append(row | {"status": "rejected_by_risk", "error": why})
                continue
            state = state.after(it, prices[it.symbol])
            if self.dry_run:
                self._record(row | {"status": "dry_run"})
                rows.append(row | {"status": "dry_run"})
                continue
            self._record(row | {"status": "submitting"})  # on disk before the request leaves
            try:
                order = self._send(broker, it, coid)
            except Rejected as e:
                self._record(row | {"status": "rejected", "error": str(e), "updated_at": now})
                rows.append(row | {"status": "rejected"})
                continue
            self._apply(row, order, now)
            rows.append(row | {"status": order.status})
            if it.side == "buy" and it.stop is not None and order.status not in ("rejected", "canceled"):
                insert(self.conn, "stops", {"broker": row["broker"], "symbol": it.symbol, "stop": it.stop,
                                            "entry_ts": now}, replace=True)
        return rows

    def _send(self, broker, intent, coid):
        """Retry only what is safe: rate limits (nothing was sent) and ambiguous failures, each time
        looking the order up by its client_order_id first so it is never submitted twice."""
        for attempt in range(1, ATTEMPTS + 1):
            try:
                return broker.get_order(coid) or broker.place_order(intent, coid)
            except (RateLimited, Ambiguous) as e:
                if attempt == ATTEMPTS:
                    log.critical("giving up on %s after %d attempts: %s", coid, ATTEMPTS, e)
                    raise CycleAborted(f"{coid}: {e}") from e
                log.warning("%s attempt %d/%d failed (%s); retrying", coid, attempt, ATTEMPTS, e)
                self.sleep(BACKOFF_S * 2 ** (attempt - 1))

    def reconcile(self, now) -> dict[str, str]:
        """The broker (or the simulated ledger) is the source of truth: settle waiting simulated orders,
        refresh every non-final local order, and drop stops for positions that no longer exist."""
        report = {}
        for name, broker in self.router.available().items():
            changed = {o.client_order_id: o for o in broker.sync(now)}
            marks = ",".join("?" * len(TERMINAL))
            for r in self.conn.execute(f"SELECT * FROM orders WHERE asset_class = ? AND dry_run = 0 AND status NOT IN "
                                       f"({marks})", (name, *TERMINAL)).fetchall():
                order = changed.get(r["client_order_id"]) or broker.get_order(r["client_order_id"])
                if order is None:
                    if r["status"] == "submitting":  # never reached the broker (crash or abort mid-request)
                        self._record(dict(r) | {"status": "not_submitted", "updated_at": now})
                    continue
                if order.status != r["status"] or order.fills:
                    self._apply(dict(r), order, now)
            keep = set(broker.get_positions()) | {o.symbol for o in broker.get_open_orders() if o.side == "buy"}
            for s in [row["symbol"] for row in self.conn.execute("SELECT symbol FROM stops WHERE broker = ?",
                                                                 (type(broker).__name__,))]:
                if s not in keep:
                    self.conn.execute("DELETE FROM stops WHERE broker = ? AND symbol = ?", (type(broker).__name__, s))
            report[name] = f"{len(changed)} simulated orders settled, {len(keep)} positions/pending entries"
        return report

    def kill(self, now) -> dict[str, str]:
        """Cancel every open order and flatten every position on every broker; latch the bot first.
        A failing broker is reported and never stops the others."""
        if not self.dry_run:
            set_halted(self.conn, True, now)
        out = {}
        for name, broker in self.router.brokers.items():
            if isinstance(broker, Exception):
                out[name] = f"FAILED: broker unavailable ({broker})"
                continue
            try:
                if self.dry_run:
                    out[name] = (f"DRY RUN: would cancel {len(broker.get_open_orders())} open orders and flatten "
                                 f"{len(broker.get_positions())} positions")
                    continue
                cancelled = broker.cancel_all()
                orders = broker.flatten_all(f"{self.env}-kill-{now:%Y%m%d%H%M%S}")
                for o in orders:
                    self._record({"client_order_id": o.client_order_id, "cycle_id": "killswitch", "env": self.env,
                                  "broker": type(broker).__name__, "asset_class": name, "symbol": o.symbol,
                                  "side": "sell", "qty": o.qty, "order_type": "market", "limit_price": None,
                                  "time_in_force": "day", "status": o.status, "reason": "kill switch: flatten all",
                                  "created_at": now, "updated_at": now, "dry_run": 0, "error": None,
                                  "broker_order_id": o.broker_order_id})
                when = "" if broker.realtime else " (simulated: they fill at the next session's open)"
                out[name] = f"cancelled {cancelled} open orders; {len(orders)} market sell orders to flatten{when}"
            except Exception as e:
                log.critical("kill switch failed for %s: %s", name, e)
                out[name] = f"FAILED: {type(e).__name__}: {e}"
        return out
