"""decide(): the single decision path shared by the backtester and the runner.

visible bars -> strategies -> fusion (sentiment, holdings) -> risk sizing and caps -> order intents.
Research side: it only produces intents and never imports execution code.
"""
from collections import defaultdict
from dataclasses import dataclass, replace

from bot.config import load_class
from bot.domain import Decision, OrderIntent
from bot.fusion.fusion import SignalFusion
from bot.market.costs import fee
from bot.market.rules import round_price, round_qty
from bot.risk.sizing import atr, max_position_value, risk_qty, stop_price


@dataclass
class AssetContext:
    name: str
    cfg: object  # bot.config.AssetClass
    strategies: list
    fusion: SignalFusion


def make_context(name, cfg, fusion_cfg, params: dict | None = None) -> AssetContext:
    """Instantiate the asset class's strategies; `params` overrides config defaults (walk-forward only)."""
    return AssetContext(name, cfg, [load_class(s.class_path)(s, params) for s in cfg.strategies],
                        SignalFusion(fusion_cfg))


def visible(bars: dict, as_of) -> dict:
    """Only bars closed at or before as_of: everything a decision at as_of may see."""
    return {s: df.iloc[: df.index.searchsorted(as_of, side="right")] for s, df in bars.items()}


def blend(strategies, bars, as_of):
    """Weighted mean of the strategies' signals per symbol, plus their explanations."""
    total, weight, notes = defaultdict(float), defaultdict(float), defaultdict(list)
    for st in strategies:
        for sym, value in st.generate_signals(bars, as_of).items():
            total[sym] += st.cfg.weight * value
            weight[sym] += st.cfg.weight
            if st.notes.get(sym):
                notes[sym].append(st.notes[sym])
    return {s: total[s] / weight[s] for s in total}, {s: "; ".join(n) for s, n in notes.items()}


def decide(ctx, as_of, bars, book, regime, sentiment, cad_per_unit, quotes=None):
    """Return (a Decision for every symbol, order intents with sells first).

    sentiment: {symbol: (score, articles)} or None. quotes: {symbol: (bid, ask)} for live limit prices;
    without them limits are set off the last close. Entries are sized at 1x costs."""
    cfg, risk, rules = ctx.cfg, ctx.cfg.risk, ctx.cfg.rules
    view = {s: df for s, df in visible(bars, as_of).items() if len(df)}
    closes = {s: float(df["close"].iloc[-1]) for s, df in view.items()}
    signals, notes = blend(ctx.strategies, view, as_of)
    equity = book.equity(closes)
    gross_cap = min(risk.max_gross_exposure, regime.cap)
    pos_cap = max_position_value(equity, risk.max_position, cad_per_unit)
    market = all(s.order_type == "market" for s in cfg.strategies)  # limit orders unless every strategy opted in
    decisions, sells, buys, holds = [], [], [], {}

    def intent(sym, side, qty, why, stop=None):
        bid, ask = (quotes or {}).get(sym, (closes[sym], closes[sym]))
        offset = cfg.orders.limit_offset_bps["entry" if side == "buy" else "exit"] / 1e4
        limit = None if market else round_price(ask * (1 + offset) if side == "buy" else bid * (1 - offset), rules, side)
        return OrderIntent(ctx.name, sym, side, qty, "market" if market else "limit", limit,
                           cfg.orders.time_in_force, why, stop)

    def record(sym, action, why, qty=0.0):
        score, n = (sentiment or {}).get(sym) or (None, 0)
        decisions.append(Decision(sym, action, why, qty, signals.get(sym), score, n))

    fused = {s: ctx.fusion.fuse(signals.get(s), notes.get(s, ""), (sentiment or {}).get(s), book.positions.get(s),
                                closes[s]) for s in sorted(closes)}
    for sym, f in fused.items():
        if f.action == "EXIT":
            sells.append(intent(sym, "sell", book.positions[sym].qty, f.reason))
            record(sym, "EXIT", f.reason)
        elif f.action == "HOLD":
            holds[sym] = book.positions[sym].qty * closes[sym]
        elif f.action != "ENTER":
            record(sym, f.action, f.reason)

    # Held positions keep their size; trim only when a cap is exceeded by more than the rebalance band.
    scale = min(1.0, gross_cap * equity / sum(holds.values())) if holds else 1.0
    for sym, value in list(holds.items()):
        qty, target = book.positions[sym].qty, min(value * scale, pos_cap)
        cut = qty - round_qty(target / closes[sym], closes[sym], rules)  # round what stays DOWN: end within the cap
        if value - target > risk.rebalance_band * equity and cut > 0:
            which = f"gross {gross_cap:.0%} (regime {regime.label})" if value * scale < pos_cap else "per-symbol"
            why = f"TRIM {cut:g} to the {which} cap | {fused[sym].reason}"
            sells.append(intent(sym, "sell", cut, why))
            record(sym, "TRIM", why, qty - cut)
            holds[sym] = (qty - cut) * closes[sym]
        else:
            record(sym, "HOLD", fused[sym].reason, qty)

    room, cash, slots = gross_cap * equity - sum(holds.values()), book.cash, risk.max_positions - len(holds)
    for sym in sorted((s for s, f in fused.items() if f.action == "ENTER"), key=lambda s: (-signals[s], s)):
        f, close, a = fused[sym], closes[sym], atr(view[sym], risk.atr_period)
        if slots <= 0 or a <= 0:
            record(sym, "SKIP", f"{f.reason} | skipped: {'max positions reached' if slots <= 0 else 'no ATR'}")
            continue
        order = intent(sym, "buy", 0.0, "")
        px = order.limit_price or close
        sizes = {"risk budget": risk_qty(equity, risk.risk_per_trade, a, risk.stop_atr_multiple) * f.multiplier,
                 "per-symbol cap": pos_cap / close,
                 f"gross cap {gross_cap:.0%}": max(room, 0.0) / close,
                 "cash": max(cash - fee(cfg.costs, cash / px, px), 0.0) / px}
        binding = min(sizes, key=sizes.get)
        qty = round_qty(sizes[binding], close, rules)
        value_cad = qty * close * cad_per_unit
        if value_cad < risk.min_order_cad:
            record(sym, "SKIP", f"{f.reason} | skipped: {value_cad:,.0f} CAD < minimum order "
                                f"{risk.min_order_cad:,.0f} ({binding} binds)")
            continue
        stop = stop_price(close, a, risk.stop_atr_multiple)
        why = (f"{f.reason} | regime {regime.label} | {qty:g} @ ~{close:.2f} = {value_cad:,.0f} CAD "
               f"({binding} binds) | stop {stop:.2f} = close - {risk.stop_atr_multiple:g}x ATR {a:.2f}")
        buys.append(replace(order, qty=qty, reason=why, stop=stop))
        record(sym, "ENTER", why, qty)
        room -= qty * close
        cash -= qty * px + fee(cfg.costs, qty, px)
        slots -= 1
    return decisions, sells + buys
