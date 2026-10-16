import time

import pandas as pd
import pytest

from bot.sentiment import archive
from bot.sentiment.scoring import finbert
from bot.sentiment.sources import alpaca_news, canada_rss
from bot.sentiment.sources.base import Document, load_sources
from bot.sentiment.sources.reddit import Reddit

T0 = pd.Timestamp("2026-10-01 12:00", tz="UTC")


class FakeFeed(dict):
    def __init__(self, entries, status=200, bozo=False):
        super().__init__(status=status)
        self.entries, self.bozo = entries, bozo


def test_canada_rss_parses_google_news_and_tags_macro(settings, monkeypatch):
    entry = {"title": "Royal Bank beats estimates - Reuters", "link": "https://x/1", "summary": "<a>RBC</a> &amp; more",
             "published_parsed": time.gmtime(T0.timestamp()), "source": {"title": "Reuters"}}
    calls = []
    monkeypatch.setattr(canada_rss.feedparser, "parse", lambda url: calls.append(url) or FakeFeed([entry]))
    monkeypatch.setattr(canada_rss.time, "sleep", lambda s: None)
    src = canada_rss.CanadaRss("tsx", {"RY.TO": "Royal Bank of Canada"}, ["Bank of Canada"], ["https://boc/feed"])
    docs = src.fetch(["RY.TO", "TD.TO"], None, None)
    assert len(calls) == 3 and "%22Royal%20Bank%20of%20Canada%22" in calls[0]
    assert [d.symbols for d in docs] == [("RY.TO",), ("MACRO",), ("MACRO",)]
    assert docs[0].published_at == T0 and docs[0].summary == "RBC  & more" and docs[0].source == "google_news:Reuters"


def test_failed_feed_is_skipped(monkeypatch):
    monkeypatch.setattr(canada_rss.feedparser, "parse", lambda url: FakeFeed([], status=503))
    monkeypatch.setattr(canada_rss.time, "sleep", lambda s: None)
    assert canada_rss.CanadaRss("tsx", {"RY.TO": "RBC"}, [], []).fetch(["RY.TO"]) == []


def test_alpaca_news_maps_crypto_symbols(monkeypatch):
    class News:
        source, headline, summary, url, created_at = "benzinga", "Bitcoin rallies", "", "u", "2026-10-01T12:00:00Z"
        symbols = ["BTCUSD", "COIN"]

    class FakeClient:
        def __init__(self, api_key, secret_key):
            assert api_key == "paper-key-id"

        def get_news(self, request):
            assert request.symbols == "BTCUSD,ETHUSD"
            return type("NewsSet", (), {"data": {"news": [News()]}})()

    monkeypatch.setenv("ALPACA_PAPER_KEY", "paper-key-id")
    monkeypatch.setenv("ALPACA_PAPER_SECRET", "paper-secret")
    monkeypatch.setattr(alpaca_news, "NewsClient", FakeClient)
    docs = alpaca_news.AlpacaNews("crypto").fetch(["BTC/USD", "ETH/USD"], None, None)
    assert [(d.symbols, d.published_at) for d in docs] == [(("BTC/USD",), T0)]


def test_alpaca_news_without_keys_raises_missing_credentials(monkeypatch):
    from bot.config import MissingCredentials
    monkeypatch.delenv("ALPACA_PAPER_KEY", raising=False)
    monkeypatch.delenv("ALPACA_PAPER_SECRET", raising=False)
    with pytest.raises(MissingCredentials):
        alpaca_news.AlpacaNews("crypto").fetch(["BTC/USD"], None, None)


def test_reddit_is_a_stub_and_disabled(settings):
    with pytest.raises(NotImplementedError):
        Reddit("tsx").fetch([], None, None)
    assert "Reddit" not in [type(s).__name__ for s in load_sources(settings)]


def doc(headline, symbols=("RY.TO",), published=T0, source="google_news:X"):
    return Document(source, published, symbols, headline, "", "https://x/" + headline)


def test_archive_keeps_first_seen_merges_symbols_and_never_backfills_rss(tmp_path):
    conn = archive.connect(tmp_path / "news.sqlite")
    seen1, seen2 = T0 + pd.Timedelta(days=5), T0 + pd.Timedelta(days=9)
    assert archive.store(conn, [doc("RBC up")], False, seen1) == 1
    assert archive.store(conn, [doc("RBC up", ("BMO.TO",))], False, seen2) == 0  # same article, new symbol
    archive.store(conn, [doc("BTC up", ("BTC/USD",), source="alpaca_news:benzinga")], True, seen2)
    ids = [i for i, _ in archive.unscored(conn, "s")]
    archive.save_scores(conn, "s", ids, [(0.5, 0.9)] * len(ids), seen2)
    df = archive.frame(conn, "s").set_index("symbol")
    assert set(df.index) == {"RY.TO", "BMO.TO", "BTC/USD"}
    assert df.loc["RY.TO", "first_seen_at"] == seen1  # first sighting survives re-fetching
    assert df.loc["RY.TO", "available_at"] == seen1  # RSS: usable only once the bot had it
    assert df.loc["BTC/USD", "available_at"] == T0  # point-in-time vendor: publish time
    assert archive.unscored(conn, "s") == []


def test_llm_scorer_is_excluded_before_its_training_cutoff(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "ROOT", tmp_path)
    conn = archive.connect(tmp_path / settings.paths.news_db)
    archive.store(conn, [doc("old news", published=T0 - pd.Timedelta(days=400)), doc("new news")], True, T0)
    ids = [i for i, _ in archive.unscored(conn, archive.scorer_key(settings))]
    archive.save_scores(conn, archive.scorer_key(settings), ids, [(0.1, 1.0)] * 2, T0)
    assert len(archive.load_index(settings).by_symbol["RY.TO"][0]) == 2
    monkeypatch.setattr(finbert.FinBert, "knowledge_cutoff", (T0 - pd.Timedelta(days=30)).date())
    assert len(archive.load_index(settings).by_symbol["RY.TO"][0]) == 1  # only post-cutoff news survives


def test_coverage_says_not_backtestable_when_archive_starts_after_the_period(settings):
    from bot.backtest.engine import Sleeve
    from bot.sentiment.aggregate import NewsIndex
    news = NewsIndex(pd.DataFrame({"symbol": ["RY.TO"], "available_at": [T0], "headline": ["x"], "score": [0.1],
                                   "confidence": [1.0]}), 0.8)
    sl = Sleeve("tsx", settings.asset_classes["tsx"], {"RY.TO": None})
    cov = archive.coverage(news, settings, sl, "2025-01-01", "2026-06-30")
    assert cov["from"] is None and "not backtestable" in cov["text"]


def test_finbert_score_is_p_positive_minus_p_negative(monkeypatch):
    import torch

    class Tok:
        def __call__(self, texts, **kw):
            return type("Enc", (dict,), {"to": lambda self, d: self})(input_ids=torch.zeros(len(texts), 3))

    class Model:
        config = type("C", (), {"id2label": {0: "positive", 1: "negative", 2: "neutral"}})()

        def to(self, device):
            return self

        def eval(self):
            return self

        def __call__(self, **enc):
            logits = torch.log(torch.tensor([[0.7, 0.2, 0.1]] * enc["input_ids"].shape[0]))
            return type("Out", (), {"logits": logits})()

    import transformers
    monkeypatch.setattr(transformers.AutoTokenizer, "from_pretrained", lambda m: Tok())
    monkeypatch.setattr(transformers.AutoModelForSequenceClassification, "from_pretrained", lambda m: Model())
    scores = finbert.FinBert("fake/model").score(["a", "b", "c"])
    assert scores == [pytest.approx((0.5, 0.7))] * 3 and finbert.FinBert.knowledge_cutoff is None
