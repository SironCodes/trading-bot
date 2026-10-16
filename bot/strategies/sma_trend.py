"""v1 trend strategy (daily bars, swing horizon): long when close > SMA(fast) > SMA(slow) and the
`momentum`-bar return is positive; exit when close < SMA(fast). The ATR stop lives in risk/."""
from bot.strategies.base import Strategy


class SmaTrend(Strategy):
    @property
    def lookback(self) -> int:
        return max(self.params["slow"], self.params["momentum"] + 1)

    def generate_signals(self, bars, as_of):
        fast, slow, mom = self.params["fast"], self.params["slow"], self.params["momentum"]
        signals, self.notes = {}, {}
        for sym, df in bars.items():
            close = df["close"].to_numpy()
            if len(close) < self.lookback:
                continue
            c, sma_f, sma_s = close[-1], close[-fast:].mean(), close[-slow:].mean()
            ret = c / close[-1 - mom] - 1
            if c < sma_f:
                signals[sym] = -1.0
            elif c > sma_f > sma_s and ret > 0:
                signals[sym] = min(1.0, ret)
            else:
                signals[sym] = 0.0
            self.notes[sym] = f"close {c:.2f}, SMA{fast} {sma_f:.2f}, SMA{slow} {sma_s:.2f}, r{mom} {ret:+.1%}"
        return signals
