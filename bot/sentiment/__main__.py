"""Fetch news from every enabled source into the archive, then score new documents.

Usage: python -m bot.sentiment   (schedule it hourly: archive coverage only starts the day it first runs)
"""
import logging
import os

import pandas as pd
from dotenv import load_dotenv

from bot.config import ROOT, load_settings
from bot.sentiment import archive
from bot.sentiment.ingest import archive_news, score_new


def main() -> None:
    load_dotenv(ROOT / ".env")
    s = load_settings()
    logging.basicConfig(level=s.logging.level, format="%(levelname)s %(name)s: %(message)s")
    now = pd.Timestamp.now(tz="UTC")
    for name, result in archive_news(s, now).items():
        print(f"{name}: {result}")
    print(f"scored {score_new(s, now)} new documents with {s.sentiment.scorer.model}")
    with archive.connect(ROOT / s.paths.news_db) as conn:
        total, first = conn.execute("SELECT COUNT(*), MIN(first_seen_at) FROM news_documents").fetchone()
    print(f"archive: {total} documents, first seen {first}; Alpaca paper keys present: "
          f"{bool(os.environ.get('ALPACA_PAPER_KEY'))}")


if __name__ == "__main__":
    main()
