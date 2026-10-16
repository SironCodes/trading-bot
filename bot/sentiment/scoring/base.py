"""SentimentScorer interface."""
import datetime as dt
from abc import ABC, abstractmethod


class SentimentScorer(ABC):
    """score(texts) -> [(score in [-1, 1], confidence in [0, 1])].

    knowledge_cutoff: any LLM-based scorer MUST set its training cutoff. Backtests then refuse to use it on
    news published before that date, because the model may already know what happened next."""
    name = "scorer"
    knowledge_cutoff: dt.date | None = None

    @abstractmethod
    def score(self, texts: list[str]) -> list[tuple[float, float]]:
        ...
