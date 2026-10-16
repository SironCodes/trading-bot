"""Alpaca News API (Benzinga) for crypto symbols. Uses the PAPER keys: market data does not depend on
the trading environment, and this project never reads live credentials for data."""
import os

import pandas as pd
from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest

from bot.config import alpaca_credentials
from bot.sentiment.sources.base import Document, SentimentSource


class AlpacaNews(SentimentSource):
    source_prefix = "alpaca_news"

    def __init__(self, asset_class, point_in_time_history=True, history_start="2021-01-01"):
        super().__init__(asset_class, point_in_time_history)
        self.history_start = pd.Timestamp(history_start, tz="UTC")  # first backfill starts here

    def fetch(self, symbols, start, end):
        key, secret = alpaca_credentials("paper", os.environ)
        client = NewsClient(api_key=key, secret_key=secret.get_secret_value())
        to_bot = {s.replace("/", ""): s for s in symbols}  # the news API names BTC/USD "BTCUSD"
        req = NewsRequest(symbols=",".join(to_bot), start=(start or self.history_start).to_pydatetime(),
                          end=end.to_pydatetime() if end is not None else None, include_content=False)
        docs = []
        for n in client.get_news(req).data.get("news", []):  # alpaca-py follows next_page_token itself
            syms = tuple(sorted({to_bot[s] for s in n.symbols if s in to_bot}))
            if syms:
                docs.append(Document(f"alpaca_news:{n.source}", pd.Timestamp(n.created_at).tz_convert("UTC"),
                                     syms, n.headline, n.summary or "", n.url or ""))
        return docs
