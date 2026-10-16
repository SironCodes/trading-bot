"""Plain data types shared by research and execution code."""
from dataclasses import dataclass, field

import pandas as pd

QTY_EPS = 1e-9  # quantities below this are treated as zero (float residue after partial sells)


@dataclass(frozen=True)
class OrderIntent:
    """What the decision pipeline wants traded. Only the execution layer turns it into a broker order."""
    asset_class: str
    symbol: str
    side: str  # "buy" | "sell"
    qty: float
    order_type: str  # "limit" | "market"
    limit_price: float | None
    time_in_force: str
    reason: str
    stop: float | None = None  # protective stop to record if this order opens a position


@dataclass
class Position:
    qty: float
    avg_price: float
    stop: float | None = None
    entry_ts: pd.Timestamp | None = None


@dataclass
class Book:
    """One asset class's account, in that asset class's currency."""
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    prev_equity: float | None = None  # equity at the previous cycle: the daily-loss reference

    def gross(self, prices: dict[str, float]) -> float:
        return sum(p.qty * prices.get(s, p.avg_price) for s, p in self.positions.items())

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.gross(prices)

    def apply(self, symbol: str, side: str, qty: float, price: float, fee: float, ts=None, stop=None) -> None:
        """Book a fill. Buys average into the position; sells reduce it and drop it when flat."""
        pos = self.positions.get(symbol)
        if side == "buy":
            if pos is None:
                self.positions[symbol] = Position(qty, price, stop, ts)
            else:
                pos.avg_price = (pos.avg_price * pos.qty + price * qty) / (pos.qty + qty)
                pos.qty += qty
            self.cash -= qty * price + fee
        else:
            pos.qty -= qty
            self.cash += qty * price - fee
            if pos.qty <= QTY_EPS:
                del self.positions[symbol]


@dataclass(frozen=True)
class Decision:
    symbol: str
    action: str  # ENTER | EXIT | HOLD | TRIM | VETO | SKIP | FLAT
    reason: str
    target_qty: float = 0.0
    signal: float | None = None
    sentiment: float | None = None
    articles: int = 0
