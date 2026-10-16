"""Venue order rules: TSX tick sizes and board lots; fractional size steps for crypto."""
import math

EPS = 1e-9  # guards floor/ceil against float residue (e.g. 0.3 / 0.1 = 2.9999999999999996)


def _step(price: float, steps) -> float:
    """Size of the first step whose from_price <= price, scanning from the highest band down."""
    return next(s.size for s in sorted(steps, key=lambda s: -s.from_price) if price >= s.from_price)


def round_price(price: float, rules, side: str) -> float:
    """Snap a limit price to the tick grid. Buys round down, sells round up, so a limit is never
    looser than intended. Assets without a tick grid (crypto) are left for the broker adapter."""
    if not rules.tick:
        return price
    tick = _step(price, rules.tick)
    n = math.floor(price / tick + EPS) if side == "buy" else math.ceil(price / tick - EPS)
    return round(n * tick, 10)


def round_qty(qty: float, price: float, rules) -> float:
    """Round an order size down to what the venue accepts: whole shares or board lots on TSX
    (the lot depends on the price), a size step for fractional assets."""
    if qty <= 0:
        return 0.0
    if rules.qty_step:
        return round(math.floor(qty / rules.qty_step + EPS) * rules.qty_step, 10)
    lot = _step(price, rules.board_lot) if rules.lot_policy == "board_lots" else 1
    return float(math.floor(qty / lot + EPS) * lot)
