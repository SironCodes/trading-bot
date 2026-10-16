"""Event-driven daily backtester.

Decision times of all asset classes are replayed in order. Each decision calls the SAME pipeline.decide,
risk.limits.check_order and market.fills code as the runner. An order placed at a decision fills at the
open of the next bar (TSX: the next session; crypto: the UTC day that just began) or expires unfilled.
Each asset class has its own book; equity is also tracked in CAD for the combined view. No lookahead:
decisions see bars closed at or before the decision, news available strictly before it, and the prior
business day's USD/CAD rate. This module never imports execution code.
"""
from dataclasses import dataclass, field

import pandas as pd

from bot.data.fx import rate_at
from bot.domain import Book
from bot.market.calendar import decision_times
from bot.market.costs import fee as trade_fee
from bot.market.costs import slipped
from bot.market.fills import fill
from bot.market.rules import round_qty
from bot.pipeline import decide, make_context, visible
from bot.regime.macro import MacroRegime
from bot.risk.limits import AccountState, check_order

MACRO = "MACRO"  # pseudo-symbol under which macro news is archived


@dataclass
class Sleeve:
    name: str
    cfg: object  # bot.config.AssetClass
    bars: dict  # eligible symbols, usable history
    schedule: list = field(default_factory=list)  # [(effective calendar date, params)]; empty = config params


@dataclass
class SleeveResult:
    name: str
    currency: str
    equity: pd.Series  # own currency, at each decision
    equity_cad: pd.Series
    exposure: pd.Series  # gross / equity
    regime: pd.Series
    trades: pd.DataFrame
    orders: pd.DataFrame
    decisions: pd.DataFrame
    fees_cad: float
    slippage_cad: float
    traded_cad: float


class _Run:
    """Mutable state of one asset class during a backtest."""

    def __init__(self, sleeve, settings, cash):
        self.sl, self.settings = sleeve, settings
        self.book, self.pending, self.ctx, self.params = Book(cash), [], None, object()
        self.curve, self.trades, self.orders, self.decisions, self.open = [], [], [], [], {}
        self.fees_cad = self.slippage_cad = self.traded_cad = 0.0
        self.schedule = sorted(sleeve.schedule, key=lambda e: e[0])

    def context(self, t):
        day = t.tz_convert(self.sl.cfg.decide_at.tz).tz_localize(None).normalize()
        params = next((p for start, p in reversed(self.schedule) if pd.Timestamp(start) <= day), None)
        if params != self.params:  # walk-forward switched parameters
            self.ctx, self.params = make_context(self.sl.name, self.sl.cfg, self.settings.fusion, params), params
        return self.ctx

    def settle(self, t, cost_mult, fx_rate):
        for it, placed in self.pending:
            df = self.sl.bars[it.symbol]
            i = df.index.searchsorted(placed, side="right")  # first bar that opens after the decision
            if i >= len(df) or df.index[i] > t:
                self._log(placed, it, "expired: no bar before the next decision")
                continue
            bar = df.iloc[i]
            res = fill(it, bar, self.sl.cfg, cost_mult, self.book.cash if it.side == "buy" else None)
            if res is None:
                self._log(placed, it, "expired: not marketable at the open (or no volume / cash)")
                continue
            qty, px, fee = res
            self.book.apply(it.symbol, it.side, qty, px, fee, df.index[i], it.stop)
            self._track(it, qty, px, fee, df.index[i], fx_rate)
            slip = abs(px - float(bar["open"])) * qty
            self.fees_cad += fee * fx_rate
            self.slippage_cad += slip * fx_rate
            self.traded_cad += qty * px * fx_rate
            self._log(placed, it, "filled" if qty >= it.qty else f"partial: {qty:g} of {it.qty:g}", qty, px, fee,
                      df.index[i])
        self.pending = []

    def _track(self, it, qty, px, fee, ts, fx_rate):
        tr = self.open.get(it.symbol)
        if it.side == "buy":
            if tr is None:
                tr = self.open[it.symbol] = {"symbol": it.symbol, "entry_date": ts, "entry_reason": it.reason,
                                             "bought": 0.0, "cost": 0.0, "sold": 0.0, "proceeds": 0.0, "fees": 0.0}
            tr["bought"] += qty
            tr["cost"] += qty * px + fee
        else:
            tr["sold"] += qty
            tr["proceeds"] += qty * px - fee
        tr["fees"] += fee
        if it.side == "sell" and it.symbol not in self.book.positions:  # position closed: one round trip
            pnl = tr["proceeds"] - tr["cost"]
            self.trades.append({"asset_class": self.sl.name, "symbol": it.symbol,
                                "entry_date": tr["entry_date"].date(), "exit_date": ts.date(),
                                "holding_days": (ts - tr["entry_date"]).days, "qty": tr["bought"],
                                "avg_entry": tr["cost"] / tr["bought"], "avg_exit": tr["proceeds"] / tr["sold"],
                                "pnl": pnl, "pnl_cad": pnl * fx_rate, "return": pnl / tr["cost"], "fees": tr["fees"],
                                "entry_reason": tr["entry_reason"], "exit_reason": it.reason})
            del self.open[it.symbol]

    def _log(self, placed, it, status, qty=0.0, px=None, fee=0.0, bar_ts=None):
        self.orders.append({"asset_class": self.sl.name, "decided_at": placed, "symbol": it.symbol, "side": it.side,
                            "qty": it.qty, "order_type": it.order_type, "limit_price": it.limit_price,
                            "time_in_force": it.time_in_force, "status": status, "filled_qty": qty,
                            "fill_price": px, "fee": fee, "fill_bar_close": bar_ts, "reason": it.reason})

    def result(self, keep_decisions):
        curve = pd.DataFrame(self.curve).set_index("ts")
        return SleeveResult(self.sl.name, self.sl.cfg.currency, curve["equity"], curve["equity_cad"],
                            curve["exposure"], curve["regime"], pd.DataFrame(self.trades), pd.DataFrame(self.orders),
                            pd.DataFrame(self.decisions if keep_decisions else []),
                            self.fees_cad, self.slippage_cad, self.traded_cad)


def run(settings, sleeves, start, end, cost_mult=1.0, fx=None, regime_bars=None, news=None, sentiment_from=None,
        keep_decisions=False) -> dict[str, SleeveResult]:
    """Backtest `sleeves` from `start` to `end` (calendar dates). `news` (a NewsIndex) enables sentiment
    from `sentiment_from` on; without it fusion sees no sentiment and the regime's macro term is 0."""
    model, s_cfg, regime_bars = MacroRegime(settings.regime), settings.sentiment, regime_bars or {}
    runs, events = {}, []
    for sl in sleeves:
        times = decision_times(sl.cfg, start, end)
        if not len(times):
            continue
        rate = rate_at(fx, times[0]) if sl.cfg.currency == "USD" else 1.0
        runs[sl.name] = _Run(sl, settings, sl.cfg.capital_cad / rate)
        events += [(t, sl.name) for t in times]
    for t, name in sorted(events):
        r = runs[name]
        fx_rate = rate_at(fx, t) if r.sl.cfg.currency == "USD" else 1.0
        r.settle(t, cost_mult, fx_rate)
        view = visible(r.sl.bars, t)
        closes = {s: float(df["close"].iloc[-1]) for s, df in view.items() if len(df)}
        equity, gross = r.book.equity(closes), r.book.gross(closes)
        use_news = news is not None and (sentiment_from is None or t >= sentiment_from)
        macro = news.score(MACRO, t, settings.regime.macro.lookback_hours, s_cfg.half_life_hours) if use_news else None
        regime = model.classify(visible(regime_bars, t), macro)
        senti = news.scores(closes, t, s_cfg.lookback_hours, s_cfg.half_life_hours) if use_news else None
        r.curve.append({"ts": t, "equity": equity, "equity_cad": equity * fx_rate,
                        "exposure": gross / equity if equity > 0 else 0.0, "regime": regime.label})
        decisions, intents = decide(r.context(t), t, r.sl.bars, r.book, regime, senti, fx_rate)
        if keep_decisions:
            r.decisions += [{"ts": t, "asset_class": name, **d.__dict__} for d in decisions]
        others = [o for o in runs.values() if o is not r and o.curve]
        state = AccountState(
            equity=equity, prev_equity=r.book.prev_equity, cash=r.book.cash, gross=gross,
            held={s: p.qty for s, p in r.book.positions.items()}, open_orders=0,
            combined_open_orders=sum(len(o.pending) for o in others),
            combined_equity_cad=equity * fx_rate + sum(o.curve[-1]["equity_cad"] for o in others),
            combined_gross_cad=gross * fx_rate + sum(o.curve[-1]["exposure"] * o.curve[-1]["equity_cad"] for o in others),
            cad_per_unit=fx_rate, regime_cap=regime.cap)
        for it in intents:
            ok, why = check_order(it, closes[it.symbol], state, r.sl.cfg.risk, settings.combined_risk)
            if ok:
                r.pending.append((it, t))
                state = state.after(it, closes[it.symbol])
            else:
                r._log(t, it, why)
        r.book.prev_equity = equity
    return {name: r.result(keep_decisions) for name, r in runs.items()}


def daily(series: pd.Series) -> pd.Series:
    """Last value per UTC calendar day (one point per day for mixed TSX/crypto timelines)."""
    return series.groupby(series.index.normalize()).last()


def combined_cad(results: dict) -> pd.Series:
    """Combined CAD equity: each asset class's last known CAD value, summed per UTC day."""
    frame = pd.concat({n: r.equity_cad for n, r in results.items()}, axis=1, sort=True).ffill().dropna()
    return daily(frame.sum(axis=1))


def buy_and_hold(sleeve, symbols, times, cost_mult=1.0, fx=None) -> pd.Series:
    """Equal-weight buy-and-hold of `symbols` with the asset class's capital: bought at the first open
    after the first decision with 1x-style costs (no volume cap), valued at each decision in CAD."""
    cfg = sleeve.cfg
    rate0 = rate_at(fx, times[0]) if cfg.currency == "USD" else 1.0
    cash = cfg.capital_cad / rate0
    alloc, held = cash / len(symbols), {}
    for s in symbols:
        df = sleeve.bars[s]
        bar = df.iloc[df.index.searchsorted(times[0], side="right")]
        px = slipped(float(bar["open"]), "buy", cfg.costs, cost_mult)
        qty = round_qty((alloc - trade_fee(cfg.costs, alloc / px, px, cost_mult)) / px, px, cfg.rules)
        cash -= qty * px + trade_fee(cfg.costs, qty, px, cost_mult)
        held[s] = qty
    value = cash + sum(q * sleeve.bars[s]["close"].reindex(times, method="ffill") for s, q in held.items())
    rates = pd.Series([rate_at(fx, t) for t in times], index=times) if cfg.currency == "USD" else 1.0
    return value * rates
