"""Strategy interface. A strategy maps visible bars to signals; it never sizes or places orders.

To add a strategy: create bot/strategies/<name>.py with a Strategy subclass and add an entry with
`class: bot.strategies.<name>:<ClassName>` under an asset class's `strategies:` in settings.yaml.
"""
from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    """generate_signals(bars, as_of) -> {symbol: signal in [-1, 1]}:
      > 0  entry condition met (the magnitude only ranks entries when capacity runs out)
      0    no change: hold if held, stay out if not
      < 0  exit; the bot is long-only, so a negative signal never means "go short"
    `bars` holds only bars closed at or before as_of. Subclasses may explain each signal in self.notes."""

    def __init__(self, cfg, params: dict | None = None):
        self.cfg = cfg
        self.name = cfg.class_path.rsplit(":", 1)[-1]
        self.timeframe = cfg.timeframe
        self.holding_period_days = cfg.holding_period_days
        self.params = {**cfg.params, **(params or {})}
        self.notes: dict[str, str] = {}

    @property
    @abstractmethod
    def lookback(self) -> int:
        """Bars of history needed before a symbol gets a signal."""

    @abstractmethod
    def generate_signals(self, bars: dict[str, pd.DataFrame], as_of: pd.Timestamp) -> dict[str, float]:
        ...
