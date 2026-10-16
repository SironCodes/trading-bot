"""Pre-trade risk checks (pure). The execution layer and the backtester call check_order before every
order; sizing already respects these limits, so a rejection here means something drifted."""
from dataclasses import dataclass, replace

from bot.risk.sizing import max_position_value

TOL = 1e-9  # relative tolerance so float rounding never rejects an order sized exactly at a cap


@dataclass(frozen=True)
class AccountState:
    equity: float  # this asset class, own currency
    prev_equity: float | None  # equity at the previous cycle (daily-loss reference)
    cash: float
    gross: float  # long market value, own currency
    held: dict  # symbol -> qty
    open_orders: int
    combined_open_orders: int
    combined_equity_cad: float
    combined_gross_cad: float
    cad_per_unit: float  # CAD per unit of this asset class's currency
    regime_cap: float
    halted: bool = False  # kill-switch latch

    def after(self, intent, price: float) -> "AccountState":
        """State once `intent` is accepted (orders in the same cycle count against the limits)."""
        sign = 1 if intent.side == "buy" else -1
        value = intent.qty * price
        held = dict(self.held)
        held[intent.symbol] = held.get(intent.symbol, 0.0) + sign * intent.qty
        return replace(self, held=held, gross=self.gross + sign * value,
                       cash=self.cash - value if intent.side == "buy" else self.cash,
                       open_orders=self.open_orders + 1, combined_open_orders=self.combined_open_orders + 1,
                       combined_gross_cad=self.combined_gross_cad + sign * value * self.cad_per_unit)


def check_order(intent, price: float, s: AccountState, risk, combined) -> tuple[bool, str]:
    """Return (ok, reason). Sells only need a position to sell (long-only); they stay allowed when
    entries are blocked so risk can always be reduced."""
    held = s.held.get(intent.symbol, 0.0)
    if intent.side == "sell":
        if intent.qty > held * (1 + TOL):
            return False, f"rejected: selling {intent.qty:g} > position {held:g} (long-only, no shorting)"
        return True, "ok: reduces risk"
    value = intent.qty * (intent.limit_price or price)
    if s.halted:
        return False, "rejected: kill switch engaged; run `python -m bot.killswitch --reset` to resume"
    if s.prev_equity and s.equity / s.prev_equity - 1 <= -risk.max_daily_loss:
        return False, (f"rejected: daily loss limit hit ({s.equity / s.prev_equity - 1:+.2%} <= "
                       f"-{risk.max_daily_loss:.0%}); no new entries this cycle")
    if s.open_orders >= risk.max_open_orders:
        return False, f"rejected: {s.open_orders} open orders >= max {risk.max_open_orders}"
    if s.combined_open_orders >= combined.max_open_orders:
        return False, f"rejected: {s.combined_open_orders} open orders across asset classes >= max {combined.max_open_orders}"
    cap = max_position_value(s.equity, risk.max_position, s.cad_per_unit)
    if (held + intent.qty) * price > cap * (1 + TOL):
        return False, f"rejected: position would be {(held + intent.qty) * price:,.0f} > cap {cap:,.0f}"
    limit = min(risk.max_gross_exposure, s.regime_cap) * s.equity
    if s.gross + intent.qty * price > limit * (1 + TOL):
        return False, f"rejected: gross exposure would be {s.gross + intent.qty * price:,.0f} > {limit:,.0f}"
    if s.combined_gross_cad + intent.qty * price * s.cad_per_unit > combined.max_gross_exposure * s.combined_equity_cad * (1 + TOL):
        return False, "rejected: combined gross exposure (CAD) would exceed its cap"
    if intent.qty * price * s.cad_per_unit < risk.min_order_cad:
        return False, f"rejected: order {intent.qty * price * s.cad_per_unit:,.0f} CAD < minimum {risk.min_order_cad:,.0f}"
    if value > s.cash * (1 + TOL):
        return False, f"rejected: order {value:,.0f} > cash {s.cash:,.0f} (no margin)"
    return True, "ok"
