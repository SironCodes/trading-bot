"""Free Canadian news: Google News RSS searched by company name, macro queries, and plain RSS feeds
(e.g. Bank of Canada press releases), the last two tagged MACRO. RSS has no deep history, so these
documents count only from the moment the bot first saw them (point_in_time_history: false)."""
import calendar
import html
import logging
import re
import time
from urllib.parse import quote, urlparse

import feedparser
import pandas as pd

from bot.sentiment.sources.base import Document, SentimentSource

log = logging.getLogger(__name__)
GOOGLE_NEWS = "https://news.google.com/rss/search?q={query}&hl=en-CA&gl=CA&ceid=CA:en"
PAUSE_S = 1.0  # politeness delay between feed requests
TAG = re.compile(r"<[^>]+>")


class CanadaRss(SentimentSource):
    def __init__(self, asset_class, names: dict, macro_queries: list, feeds: list, point_in_time_history=False):
        super().__init__(asset_class, point_in_time_history)
        self.names, self.macro_queries, self.feeds = names, macro_queries, feeds

    def jobs(self, symbols):
        jobs = [(GOOGLE_NEWS.format(query=quote(f'"{self.names[s]}"')), (s,), "google_news")
                for s in symbols if s in self.names]
        jobs += [(GOOGLE_NEWS.format(query=quote(f'"{q}"')), ("MACRO",), "google_news") for q in self.macro_queries]
        return jobs + [(url, ("MACRO",), "rss") for url in self.feeds]

    def fetch(self, symbols, start=None, end=None):
        docs = []
        for url, syms, kind in self.jobs(symbols):
            feed = feedparser.parse(url)  # never raises: check the status and bozo flag instead
            if feed.get("status", 200) >= 400 or (feed.bozo and not feed.entries):
                log.warning("feed failed (%s): %s", feed.get("status"), url)
                continue
            for e in feed.entries:
                when = e.get("published_parsed") or e.get("updated_parsed")
                if not when or not e.get("title"):
                    continue
                ts = pd.Timestamp(calendar.timegm(when), unit="s", tz="UTC")
                if (start is not None and ts < start) or (end is not None and ts > end):
                    continue
                publisher = e.get("source", {}).get("title", "") if kind == "google_news" else urlparse(url).netloc
                summary = html.unescape(TAG.sub(" ", e.get("summary", ""))).strip()
                docs.append(Document(f"{kind}:{publisher}", ts, syms, e["title"], summary, e.get("link", "")))
            time.sleep(PAUSE_S)
        return docs
