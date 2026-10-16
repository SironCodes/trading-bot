"""News ingestion: fetch every enabled source into the archive, then score new documents."""
import logging

import pandas as pd

from bot.config import ROOT, MissingCredentials, load_class
from bot.sentiment import archive
from bot.sentiment.sources.base import load_sources

log = logging.getLogger("bot.sentiment")


def archive_news(settings, now: pd.Timestamp) -> dict[str, str]:
    """One pass over all sources. A failing source is logged and skipped, never fatal."""
    results = {}
    with archive.connect(ROOT / settings.paths.news_db) as conn:
        for src in load_sources(settings):
            name = type(src).__name__
            # point-in-time vendors resume from their latest archived article; RSS takes what is listed now
            since = archive.latest_published(conn, src.source_prefix) if src.source_prefix else None
            try:
                docs = src.fetch(settings.asset_classes[src.asset_class].symbols,
                                 since - pd.Timedelta(days=1) if since is not None else None, now)
            except MissingCredentials as e:
                results[name] = f"skipped: {e}"
                continue
            except Exception as e:  # network/API errors: keep the archive, report, move on
                log.warning("%s failed: %s", name, e)
                results[name] = f"failed: {type(e).__name__}: {e}"
                continue
            results[name] = f"{len(docs)} fetched, {archive.store(conn, docs, src.point_in_time_history, now)} new"
    return results


def score_new(settings, now: pd.Timestamp) -> int:
    key, sc = archive.scorer_key(settings), settings.sentiment.scorer
    with archive.connect(ROOT / settings.paths.news_db) as conn:
        todo = archive.unscored(conn, key)
        if todo:
            scorer = load_class(sc.class_path)(sc.model, sc.device, sc.batch_size)
            archive.save_scores(conn, key, [i for i, _ in todo], scorer.score([h for _, h in todo]), now)
    return len(todo)
