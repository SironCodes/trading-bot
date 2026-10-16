"""Next-bar-open fill model, shared by the backtester and SimulatedBroker."""
from bot.market.costs import fee, slipped
from bot.market.rules import round_qty


def fill(order, bar, cfg, cost_mult: float = 1.0, cash: float | None = None):
    """Fill `order` at the open of `bar` (the first bar after the decision), or return None.

    A limit order fills only if the open is at or through its limit, and never at a worse price.
    No intrabar fills are assumed, which is conservative and matches TSX odd-lot fill-or-kill.
    Size is capped by volume participation (the source of partial fills) and, for buys, by cash.
    Returns (qty, price, fee)."""
    side, open_ = order.side, float(bar["open"])
    px = slipped(open_, side, cfg.costs, cost_mult)
    if order.order_type == "limit":
        if (open_ > order.limit_price) if side == "buy" else (open_ < order.limit_price):
            return None
        px = min(px, order.limit_price) if side == "buy" else max(px, order.limit_price)
    qty = min(order.qty, round_qty(cfg.costs.max_volume_participation * float(bar["volume"]), px, cfg.rules))
    if side == "buy" and cash is not None and qty * px + fee(cfg.costs, qty, px, cost_mult) > cash:
        qty = min(qty, round_qty((cash - fee(cfg.costs, qty, px, cost_mult)) / px, px, cfg.rules))
    if qty <= 0:
        return None
    return qty, px, fee(cfg.costs, qty, px, cost_mult)
