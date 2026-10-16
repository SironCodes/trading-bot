"""News archive (SQLite). Every fetched document is stored once with the moment the bot FIRST saw it;
re-fetching never moves that time. Backtests may only use what this archive held at each date:
  available_at = published_at                      for point-in-time vendors (Alpaca News)
  available_at = max(published_at, first_seen_at)  for everything else (RSS), so nothing is backfilled.
"""
import hashlib
import json
import sqlite3
from pathlib import Path

import pandas as pd

from bot.config import ROOT, load_class
from bot.sentiment.aggregate import NewsIndex

SCHEMA = """
CREATE TABLE IF NOT EXISTS news_documents (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, url TEXT, headline TEXT NOT NULL, summary TEXT,
    symbols TEXT NOT NULL, published_at TEXT NOT NULL, first_seen_at TEXT NOT NULL,
    point_in_time INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sentiment_scores (
    doc_id TEXT NOT NULL REFERENCES news_documents(id), scorer TEXT NOT NULL, score REAL NOT NULL,
    confidence REAL NOT NULL, scored_at TEXT NOT NULL, PRIMARY KEY (doc_id, scorer));
"""


def scorer_key(settings) -> str:
    """Scores are stored per scorer class AND model, so changing the model never mixes scores."""
    return f"{settings.sentiment.scorer.class_path}/{settings.sentiment.scorer.model}"


def connect(path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def doc_id(d) -> str:
    return hashlib.sha1(f"{d.source}|{d.url}|{d.headline}".encode()).hexdigest()


def store(conn, docs, point_in_time: bool, seen_at: pd.Timestamp) -> int:
    """Insert new documents (first_seen_at = seen_at); for known ones only merge new symbols."""
    new = 0
    for d in docs:
        i = doc_id(d)
        row = conn.execute("SELECT symbols FROM news_documents WHERE id = ?", (i,)).fetchone()
        if row:
            merged = sorted(set(json.loads(row[0])) | set(d.symbols))
            conn.execute("UPDATE news_documents SET symbols = ? WHERE id = ?", (json.dumps(merged), i))
            continue
        conn.execute("INSERT INTO news_documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (i, d.source, d.url, d.headline, d.summary, json.dumps(sorted(d.symbols)),
                      d.published_at.isoformat(), seen_at.isoformat(), int(point_in_time)))
        new += 1
    conn.commit()
    return new


def latest_published(conn, source_prefix: str) -> pd.Timestamp | None:
    row = conn.execute("SELECT MAX(published_at) FROM news_documents WHERE source LIKE ?",
                       (source_prefix + "%",)).fetchone()
    return pd.Timestamp(row[0]) if row and row[0] else None


def unscored(conn, scorer: str) -> list[tuple[str, str]]:
    return conn.execute("SELECT d.id, d.headline FROM news_documents d LEFT JOIN sentiment_scores s "
                        "ON s.doc_id = d.id AND s.scorer = ? WHERE s.doc_id IS NULL", (scorer,)).fetchall()


def save_scores(conn, scorer: str, ids, scores, scored_at: pd.Timestamp) -> None:
    conn.executemany("INSERT OR REPLACE INTO sentiment_scores VALUES (?, ?, ?, ?, ?)",
                     [(i, scorer, s, c, scored_at.isoformat()) for i, (s, c) in zip(ids, scores)])
    conn.commit()


def frame(conn, scorer: str) -> pd.DataFrame:
    """One row per (document, symbol) with the moment it became available to the bot."""
    df = pd.read_sql_query(
        "SELECT d.id, d.source, d.headline, d.symbols, d.published_at, d.first_seen_at, d.point_in_time, "
        "s.score, s.confidence FROM news_documents d JOIN sentiment_scores s ON s.doc_id = d.id "
        "WHERE s.scorer = ?", conn, params=(scorer,))
    if df.empty:
        return df
    for col in ("published_at", "first_seen_at"):
        df[col] = pd.to_datetime(df[col], utc=True, format="ISO8601")
    df["available_at"] = df["published_at"].where(df["point_in_time"] == 1,
                                                  df[["published_at", "first_seen_at"]].max(axis=1))
    df["symbol"] = df["symbols"].map(json.loads)
    return df.explode("symbol", ignore_index=True)


def load_index(settings) -> NewsIndex | None:
    """Scored archive as a NewsIndex, excluding news a scorer could have learned during training."""
    with connect(ROOT / settings.paths.news_db) as conn:
        df = frame(conn, scorer_key(settings))
    cutoff = load_class(settings.sentiment.scorer.class_path).knowledge_cutoff
    if cutoff is not None and len(df):
        df = df[df["published_at"] >= pd.Timestamp(cutoff, tz="UTC")]
    return NewsIndex(df, settings.sentiment.dedupe_jaccard) if len(df) else None


def coverage(news, settings, sleeve, start, end) -> dict:
    """Which decision dates in [start, end] have archived news for this asset class."""
    from bot.market.calendar import decision_times

    first = news.first_available(list(sleeve.bars)) if news else None
    if first is None or first.tz_convert(None).normalize() > pd.Timestamp(end):
        when = "no archived news" if first is None else f"archive starts {first.date()}"
        n = len(decision_times(sleeve.cfg, start, end))
        return {"from": None, "text": f"sentiment not backtestable for this period ({when}; nothing is backfilled): "
                                      f"0 of {n} decision days and 0 of {len(sleeve.bars)} symbols covered"}
    times = decision_times(sleeve.cfg, max(pd.Timestamp(start), first.tz_convert(None).normalize()), end)
    need, s = settings.fusion.sentiment_min_articles, settings.sentiment
    days, symbols = 0, set()
    for t in times:
        hit = [sym for sym in sleeve.bars if news.score(sym, t, s.lookback_hours, s.half_life_hours)[1] >= need]
        days += bool(hit)
        symbols |= set(hit)
    return {"from": times[0] if len(times) else None,
            "text": (f"covered from {first.date()}: {days} of {len(times)} decision days had >= {need} articles for "
                     f"at least one symbol; {len(symbols)} of {len(sleeve.bars)} symbols ever had coverage "
                     f"({', '.join(sorted(symbols)) or 'none'}). Dates before {first.date()}: sentiment not "
                     f"backtestable for this period.")}
