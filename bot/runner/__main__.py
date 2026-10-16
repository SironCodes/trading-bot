"""python -m bot.runner [--env paper|live] [--dry-run] [--as-of ISO-TIME] [--db PATH]

One pass, then exit; schedule it every 15 minutes (launchd/cron). Each pass: refresh bars, FX and news ->
reconcile with the brokers -> run every asset class's latest decision slot exactly once.
TSX decides after the close and SimulatedBroker fills at the next open; crypto decides at the configured
UTC time and goes to Alpaca (paper unless live is fully unlocked).
--dry-run logs the intended orders and sends nothing. --as-of replays a past moment (simulated brokers
only, cached data, for testing); point --db at a scratch file so replays never touch the real ledger.
"""
import argparse
import logging
import os

import pandas as pd
from dotenv import load_dotenv

from bot.config import ROOT, load_class, load_settings, resolve_env
from bot.data import fx
from bot.data.universe import load_bars, refresh_all, regime_bars
from bot.domain import Book, Position
from bot.execution.manager import CycleAborted, OrderManager
from bot.execution.router import BrokerRouter
from bot.market.calendar import expected_closes, latest_decision
from bot.pipeline import decide, make_context, visible
from bot.regime.macro import MacroRegime
from bot.risk.limits import AccountState
from bot.risk.sizing import atr, stop_price
from bot.sentiment.archive import load_index
from bot.sentiment.ingest import archive_news, score_new
from bot.storage import log as logsetup
from bot.storage.db import connect, halted, insert

log = logging.getLogger("bot.runner")
MACRO = "MACRO"


def run_cycle(s, env, conn, om, router, name, now, dry_run, news, rates, rbars) -> str:
    ac = s.asset_classes[name]
    try:
        broker = router.for_asset(name)
    except Exception as e:
        return f"skipped: broker unavailable ({type(e).__name__}: {e})"
    if not broker.realtime:
        broker.now = now  # the simulated ledger runs on the cycle's clock (matters for --as-of replays)
    slot = latest_decision(ac, now)
    cycle_id = f"{'dry-' if dry_run else ''}{env}-{name}-{slot:%Y%m%d}"
    if conn.execute("SELECT 1 FROM cycles WHERE cycle_id = ? AND status = 'done'", (cycle_id,)).fetchone():
        return f"slot {slot:%Y-%m-%d %H:%M}Z already done"
    bars, dropped = load_bars(s, name)
    want = expected_closes(ac, slot - pd.Timedelta(days=10), slot)
    want = want[want <= slot][-1]  # the bar this slot must see
    stale = sorted(sym for sym, df in bars.items() if df.index[df.index <= now].max() < want)
    if stale:
        return f"skipped: data not current for {', '.join(stale)} (need the bar closing {want:%Y-%m-%d %H:%M}Z)"
    insert(conn, "cycles", {"cycle_id": cycle_id, "env": env, "asset_class": name, "slot": slot, "as_of": now,
                            "started_at": pd.Timestamp.now(tz="UTC"), "status": "running"}, replace=True)
    view = visible(bars, now)
    closes = {sym: float(df["close"].iloc[-1]) for sym, df in view.items() if len(df)}
    fx_rate = fx.rate_at(rates, now) if ac.currency == "USD" else 1.0
    broker_name, risk = type(broker).__name__, ac.risk
    stops = {r["symbol"]: r["stop"] for r in conn.execute("SELECT symbol, stop FROM stops WHERE broker = ?", (broker_name,))}
    positions = {}
    for sym, (qty, avg) in broker.get_positions().items():
        stop = stops.get(sym)
        if stop is None and sym in view:  # a position with no recorded stop (e.g. bought by hand): adopt it
            stop = stop_price(closes[sym], atr(view[sym], risk.atr_period), risk.stop_atr_multiple)
            insert(conn, "stops", {"broker": broker_name, "symbol": sym, "stop": stop, "entry_ts": now}, replace=True)
            log.warning("adopted %s position %g with a fresh %gx ATR stop at %.2f", sym, qty, risk.stop_atr_multiple, stop)
        positions[sym] = Position(qty, avg, stop)
    prev = conn.execute("SELECT equity FROM equity_daily WHERE asset_class = ? AND date < ? ORDER BY date DESC LIMIT 1",
                        (name, f"{slot:%Y-%m-%d}")).fetchone()
    book = Book(broker.get_account().cash, positions, prev["equity"] if prev else None)
    sc = s.sentiment
    macro = news.score(MACRO, now, s.regime.macro.lookback_hours, sc.half_life_hours) if news else None
    regime = MacroRegime(s.regime).classify(visible(rbars, now), macro)
    senti = news.scores(list(closes), now, sc.lookback_hours, sc.half_life_hours) if news else None
    source = load_class(ac.data)(ac)
    quotes = source.latest_quotes(list(closes)) if broker.realtime and hasattr(source, "latest_quotes") else None
    decisions, intents = decide(make_context(name, ac, s.fusion), now, bars, book, regime, senti, fx_rate, quotes)
    insert(conn, "regime", {"cycle_id": cycle_id, "as_of": now, "label": regime.label, "score": regime.score,
                            "cap": regime.cap, "reason": regime.reason})
    for d in decisions:
        insert(conn, "decisions", {"cycle_id": cycle_id, "as_of": now, "asset_class": name, "symbol": d.symbol,
                                   "action": d.action, "target_qty": d.target_qty, "signal": d.signal,
                                   "sentiment": d.sentiment, "articles": d.articles, "reason": d.reason})
        insert(conn, "signals", {"cycle_id": cycle_id, "symbol": d.symbol, "signal": d.signal,
                                 "strategies": ",".join(st.class_path for st in ac.strategies)})
        log.info("decision %s %s: %s", d.symbol, d.action, d.reason, extra={"fields": {"decision": d.__dict__}})
    equity, gross = book.equity(closes), book.gross(closes)
    others = conn.execute("SELECT asset_class, equity_cad, gross_cad FROM equity_daily e WHERE asset_class != ? AND "
                          "date = (SELECT MAX(date) FROM equity_daily WHERE asset_class = e.asset_class)",
                          (name,)).fetchall()
    open_all = sum(len(b.get_open_orders()) for b in router.available().values())
    state = AccountState(equity=equity, prev_equity=book.prev_equity, cash=book.cash, gross=gross,
                         held={sym: p.qty for sym, p in positions.items()}, open_orders=len(broker.get_open_orders()),
                         combined_open_orders=open_all, combined_equity_cad=equity * fx_rate + sum(o["equity_cad"] for o in others),
                         combined_gross_cad=gross * fx_rate + sum(o["gross_cad"] for o in others), cad_per_unit=fx_rate,
                         regime_cap=regime.cap, halted=halted(conn))
    rows = om.submit(cycle_id, name, slot, intents, state, closes, now)
    day = slot.tz_convert(ac.decide_at.tz).strftime("%Y-%m-%d")
    insert(conn, "equity_daily", {"date": day, "asset_class": name, "equity": equity, "currency": ac.currency,
                                  "fx_cad": fx_rate, "equity_cad": equity * fx_rate, "gross_cad": gross * fx_rate},
           replace=True)
    conn.execute("UPDATE cycles SET status = 'done', finished_at = ?, note = ? WHERE cycle_id = ?",
                 (pd.Timestamp.now(tz="UTC").isoformat(), f"{len(decisions)} decisions, {len(rows)} orders", cycle_id))
    counts = pd.Series([r["status"] for r in rows], dtype=str).value_counts().to_dict()
    return (f"slot {slot:%Y-%m-%d %H:%M}Z, regime {regime.label}: {len(decisions)} decisions, orders {counts or 'none'}"
            + (f"; dropped {sorted(dropped)}" if dropped else ""))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m bot.runner", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", choices=["paper", "live"])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--as-of", type=pd.Timestamp, help="replay this moment (UTC if no offset given)")
    p.add_argument("--db")
    a = p.parse_args(argv)
    if a.as_of is not None:
        a.as_of = a.as_of.tz_localize("UTC") if a.as_of.tzinfo is None else a.as_of.tz_convert("UTC")
    load_dotenv(ROOT / ".env")
    s = load_settings()
    env = resolve_env(a.env, s.mode, os.environ)
    logsetup.setup(ROOT / s.paths.logs, s.logging.level, os.environ)
    conn = connect(a.db or ROOT / s.paths.db.format(env=env))
    now, replay = (a.as_of, True) if a.as_of is not None else (pd.Timestamp.now(tz="UTC"), False)
    log.info("runner start: env=%s dry_run=%s as_of=%s", env, a.dry_run, now)
    if not replay:  # fresh data first; failures are logged and the stale-data guard decides what can run
        try:
            log.info("data refresh: %s", refresh_all(s))
            log.info("news: %s; scored %d", archive_news(s, now), score_new(s, now))
        except Exception as e:
            log.error("refresh failed: %s", e)
    router = BrokerRouter.from_settings(s, env, conn, simulated_only=replay)
    om = OrderManager(env, router, conn, s, dry_run=a.dry_run)
    if not a.dry_run:  # dry runs never touch broker state
        log.info("reconcile: %s", om.reconcile(now))
    news, rates, rbars = load_index(s), fx.load(ROOT / s.paths.bars / "fx", s.data.fx.series), regime_bars(s)
    failed = False
    for name in s.asset_classes:
        try:
            result = run_cycle(s, env, conn, om, router, name, now, a.dry_run, news, rates, rbars)
        except CycleAborted as e:
            result, failed = f"ABORTED: {e}", True
        except Exception as e:
            log.exception("cycle %s failed", name)
            result, failed = f"ERROR: {type(e).__name__}: {e}", True
        print(f"{name}: {result}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
