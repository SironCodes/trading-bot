"""Macro regime classifier: RISK_ON / NEUTRAL / RISK_OFF -> gross exposure cap per asset class.

The exact rule (every input uses only bars closed at or before as_of):
  eq_trend  = +1 if XIU.TO close > SMA(trend_sma) else -1
  btc_trend = +1 if BTC/USD close > SMA(trend_sma) else -1
  eq_vol    = -1 if XIU.TO realised vol over vol_window bars > vol_elevated_ratio x the median of that
              rolling vol over the last vol_median_window bars, else 0 (it can only penalise)
  macro     = +1 if macro-news sentiment >= bullish_at, -1 if <= bearish_at, else 0
              (0 when sentiment is off or has fewer than min_articles articles)
  score = sum of the four; RISK_ON if score >= risk_on_at, RISK_OFF if score <= risk_off_at, else NEUTRAL.
A component without enough history counts 0. Each asset class's gross exposure is capped at
gross_caps[label] of its equity.
"""
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class RegimeState:
    label: str
    cap: float
    score: int
    components: dict
    reason: str


class MacroRegime:
    def __init__(self, cfg):  # bot.config.Regime
        self.cfg = cfg

    def _trend(self, df) -> int:
        n = self.cfg.trend_sma
        if df is None or len(df) < n:
            return 0
        close = df["close"].to_numpy()
        return 1 if close[-1] > close[-n:].mean() else -1

    def _vol(self, df) -> int:
        c = self.cfg
        if df is None or len(df) < c.vol_window + c.vol_median_window + 1:
            return 0
        rets = np.diff(np.log(df["close"].to_numpy()[-(c.vol_window + c.vol_median_window + 1):]))
        vols = np.lib.stride_tricks.sliding_window_view(rets, c.vol_window).std(axis=1, ddof=1)
        return -1 if vols[-1] > c.vol_elevated_ratio * np.median(vols[-c.vol_median_window:]) else 0

    def _macro(self, macro) -> int:
        c = self.cfg
        if not macro or macro[0] is None or macro[1] < c.macro.min_articles:
            return 0
        return 1 if macro[0] >= c.macro.bullish_at else -1 if macro[0] <= c.macro.bearish_at else 0

    def classify(self, bars: dict, macro=None) -> RegimeState:
        """`bars` must already be cut at as_of; `macro` is (score, articles) or None."""
        c = self.cfg
        comp = {"eq_trend": self._trend(bars.get(c.equity_proxy)), "btc_trend": self._trend(bars.get(c.crypto_proxy)),
                "eq_vol": self._vol(bars.get(c.equity_proxy)), "macro": self._macro(macro)}
        score = sum(comp.values())
        label = "RISK_ON" if score >= c.risk_on_at else "RISK_OFF" if score <= c.risk_off_at else "NEUTRAL"
        parts = ", ".join(f"{k} {v:+d}" for k, v in comp.items())
        return RegimeState(label, c.gross_caps[label], score, comp, f"{label} (score {score:+d}: {parts})")
