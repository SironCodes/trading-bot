"""Per-symbol sentiment as of a moment: a de-duplicated, time-decayed, confidence-weighted mean of the
scores of documents PUBLISHED within the lookback window that were also AVAILABLE to the bot strictly
before as_of. Availability guards against lookahead; the publish time measures how fresh the news is
(an old article first seen today is not fresh news). Coverage thresholds are applied by fusion and the
regime, not here."""
import re

import numpy as np
import pandas as pd

WORD = re.compile(r"[a-z0-9]+")
HOUR_NS = 3_600 * 10**9


def tokens(headline: str) -> frozenset:
    """Lower-case word set, without the trailing ' - Publisher' that news aggregators append."""
    return frozenset(WORD.findall(headline.rsplit(" - ", 1)[0].lower()))


def jaccard(a: frozenset, b: frozenset) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


class NewsIndex:
    """Scored documents per symbol, sorted by publish time.
    `docs` columns: symbol, available_at (UTC), headline, score, confidence, optional published_at
    (defaults to available_at)."""

    def __init__(self, docs: pd.DataFrame, dedupe_jaccard: float):
        self.dedupe = dedupe_jaccard
        self.by_symbol = {}
        if "published_at" not in docs:
            docs = docs.assign(published_at=docs["available_at"])
        for sym, g in docs.sort_values("published_at").groupby("symbol"):
            self.by_symbol[sym] = (pd.DatetimeIndex(g["published_at"]).as_unit("ns").asi8,
                                   pd.DatetimeIndex(g["available_at"]).as_unit("ns").asi8,
                                   [tokens(h) for h in g["headline"]], g["score"].to_numpy(float),
                                   g["confidence"].to_numpy(float))

    def first_available(self, symbols) -> pd.Timestamp | None:
        firsts = [self.by_symbol[s][1].min() for s in symbols if s in self.by_symbol]
        return pd.Timestamp(min(firsts), tz="UTC") if firsts else None

    def score(self, symbol: str, as_of: pd.Timestamp, lookback_hours: float, half_life_hours: float):
        """(score in [-1, 1] or None, number of distinct articles)."""
        entry = self.by_symbol.get(symbol)
        if entry is None:
            return None, 0
        published, available, toks, scores, conf = entry
        now = pd.Timestamp(as_of).value
        lo = np.searchsorted(published, now - int(lookback_hours * HOUR_NS), "left")
        hi = np.searchsorted(published, now, "left")
        kept = []
        for i in range(lo, hi):  # visible strictly before as_of; keep the earliest of near-identical headlines
            if available[i] < now and all(jaccard(toks[i], toks[j]) < self.dedupe for j in kept):
                kept.append(i)
        if not kept:
            return None, 0
        age_h = (now - published[kept]) / HOUR_NS
        w = conf[kept] * 0.5 ** (age_h / half_life_hours)
        if w.sum() <= 0:
            return None, len(kept)
        return float((w * scores[kept]).sum() / w.sum()), len(kept)

    def scores(self, symbols, as_of, lookback_hours, half_life_hours) -> dict:
        return {s: self.score(s, as_of, lookback_hours, half_life_hours) for s in symbols}
