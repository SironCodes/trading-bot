"""Trading costs, scaled by a stress multiplier (reports run 1x and 2x)."""


def fee(costs, qty: float, price: float, mult: float = 1.0) -> float:
    """Commission (per share with a per-order minimum) or venue fee (bps of traded value), in the
    asset's currency. Alpaca charges buys in the coin received; valuing it in USD is equivalent."""
    if qty <= 0:
        return 0.0
    if costs.commission:
        return max(costs.commission.min_per_order, costs.commission.per_share * qty) * mult
    if costs.fee_bps:
        return qty * price * costs.fee_bps[costs.charge] / 1e4 * mult
    return 0.0


def slipped(price: float, side: str, costs, mult: float = 1.0) -> float:
    """Execution price after adverse slippage."""
    s = costs.slippage_bps / 1e4 * mult
    return price * (1 + s) if side == "buy" else price * (1 - s)
