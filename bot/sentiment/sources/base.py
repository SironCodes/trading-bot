"""SentimentSource: anything that returns news documents for symbols over a time range.

To add a source: subclass SentimentSource in bot/sentiment/sources/<name>.py and list it under
`sentiment.sources` in settings.yaml with `class: bot.sentiment.sources.<name>:<ClassName>`; every
other key in that entry is passed to the constructor.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd

from bot.config import load_class


@dataclass(frozen=True)
class Document:
    source: str
    published_at: pd.Timestamp  # UTC, as reported by the publisher or vendor
    symbols: tuple  # bot symbols (RY.TO, BTC/USD) or "MACRO"
    headline: str
    summary: str
    url: str


class SentimentSource(ABC):
    source_prefix: str | None = None  # set by sources that can resume from their latest archived document

    def __init__(self, asset_class: str, point_in_time_history: bool = False):
        self.asset_class = asset_class
        # True only if the vendor's publish timestamps can be trusted for historical backfill;
        # otherwise a document counts from the moment the bot first saw it.
        self.point_in_time_history = point_in_time_history

    @abstractmethod
    def fetch(self, symbols: list[str], start: pd.Timestamp | None, end: pd.Timestamp | None) -> list[Document]:
        ...


def load_sources(settings) -> list[SentimentSource]:
    sources = []
    for spec in settings.sentiment.sources:
        options = dict(spec)
        cls = load_class(options.pop("class"))
        if options.pop("enabled", True):
            sources.append(cls(**options))
    return sources
