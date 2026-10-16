"""Reddit sentiment source: placeholder for a later version."""
from bot.sentiment.sources.base import SentimentSource


class Reddit(SentimentSource):
    def fetch(self, symbols, start, end):
        raise NotImplementedError("RedditSource is a stub; implement it in a later version")
