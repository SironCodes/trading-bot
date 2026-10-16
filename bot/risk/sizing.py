"""Position sizing (pure functions): volatility-scaled size, ATR stop, per-symbol cap."""
import numpy as np


def atr(df, period: int) -> float:
    """Average true range: simple mean of the last `period` true ranges."""
    h, l, c = (df[k].to_numpy()[-(period + 1):] for k in ("high", "low", "close"))
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    return float(tr.mean()) if len(tr) else 0.0


def risk_qty(equity: float, risk_per_trade: float, atr_value: float, stop_multiple: float) -> float:
    """Units such that a stop `stop_multiple` ATRs away loses `risk_per_trade` of equity."""
    return equity * risk_per_trade / (stop_multiple * atr_value)


def stop_price(entry_ref: float, atr_value: float, stop_multiple: float) -> float:
    return entry_ref - stop_multiple * atr_value


def stop_hit(stop: float | None, close: float) -> bool:
    """Stops are checked on daily closes; the exit fills at the next open (gap risk included)."""
    return stop is not None and close <= stop


def max_position_value(equity: float, max_position, cad_per_unit: float) -> float:
    """Per-symbol cap in the asset's currency: a fraction of equity and/or a CAD amount; tighter wins."""
    caps = []
    if max_position.pct_equity is not None:
        caps.append(max_position.pct_equity * equity)
    if max_position.cad is not None:
        caps.append(max_position.cad / cad_per_unit)
    return min(caps)
