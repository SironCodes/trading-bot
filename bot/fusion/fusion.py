"""SignalFusion: quant signal + symbol sentiment + current holding -> action, size multiplier, reason.

A quant entry is REQUIRED to open a position: sentiment alone never opens a trade. Sentiment can veto
a new entry (score <= veto threshold with enough articles) or scale it up to a cap; low coverage is
neutral (1.0x, no veto). Sentiment never adds to, trims or closes a held position.
"""
from dataclasses import dataclass

from bot.risk.sizing import stop_hit


@dataclass(frozen=True)
class Fused:
    action: str  # ENTER | EXIT | HOLD | VETO | FLAT
    multiplier: float
    reason: str


class SignalFusion:
    def __init__(self, cfg):  # bot.config.Fusion
        self.cfg = cfg

    def multiplier(self, score: float | None, articles: int) -> float:
        c = self.cfg
        if score is None or articles < c.sentiment_min_articles or score <= 0:
            return 1.0
        return 1 + (c.sentiment_scale_max - 1) * min(1.0, score / c.sentiment_scale_full_at)

    def fuse(self, signal: float | None, note: str, sentiment, position, close: float) -> Fused:
        score, n = sentiment if sentiment else (None, 0)
        if score is None:
            senti = "no sentiment"
        elif n < self.cfg.sentiment_min_articles:
            senti = f"sentiment neutral ({n} art < {self.cfg.sentiment_min_articles})"
        else:
            senti = f"sentiment {score:+.2f} ({n} art)"
        if position is not None:
            if stop_hit(position.stop, close):
                return Fused("EXIT", 0.0, f"EXIT stop hit: close {close:.2f} <= stop {position.stop:.2f}")
            if signal is not None and signal < 0:
                return Fused("EXIT", 0.0, f"EXIT trend broke: {note}")
            return Fused("HOLD", 1.0, f"HOLD no exit condition: {note or 'no signal'}")
        if signal is None:
            return Fused("FLAT", 0.0, "FLAT not enough history for a signal")
        if signal <= 0:
            return Fused("FLAT", 0.0, f"FLAT no entry: {note}")
        if score is not None and n >= self.cfg.sentiment_veto_min_articles and score <= self.cfg.sentiment_veto_below:
            return Fused("VETO", 0.0, f"VETO entry blocked by {senti} <= {self.cfg.sentiment_veto_below:+.2f}: {note}")
        m = self.multiplier(score, n)
        return Fused("ENTER", m, f"ENTER {note} | {senti} x{m:.2f}")
