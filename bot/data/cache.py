"""Parquet bar cache: one file per symbol. Updates re-read an overlap of cached bars and refetch the
full history when those bars changed (a new split/dividend adjustment rescales adjusted history)."""
import logging
from pathlib import Path

import pandas as pd

from bot.data.base import empty_bars

log = logging.getLogger(__name__)


def bars_path(folder: Path, symbol: str) -> Path:
    return Path(folder) / f"{symbol.replace('/', '-')}.parquet"


def load(folder: Path, symbol: str) -> pd.DataFrame:
    path = bars_path(folder, symbol)
    return pd.read_parquet(path) if path.exists() else empty_bars()


def save(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp)
    tmp.replace(path)  # atomic: a crash never leaves a half-written cache file


def refresh(source, folder: Path, symbols, timeframe, overlap_bars, tolerance) -> dict[str, str]:
    """Bring each symbol's cache up to date. Returns a one-line event per symbol."""
    events = {}
    for sym in symbols:
        old, note = load(folder, sym), ""
        if len(old):
            start = (old.index[-min(overlap_bars, len(old))] - pd.Timedelta(days=1)).date()
            new = source.get_bars([sym], timeframe, start)[sym]
            common = old.index.intersection(new.index)
            drift = (new.loc[common, "close"] / old.loc[common, "close"] - 1).abs().max() if len(common) else None
            if drift is not None and drift <= tolerance:
                merged = pd.concat([old[~old.index.isin(new.index)], new]).sort_index()
                save(merged, bars_path(folder, sym))
                events[sym] = f"+{len(merged) - len(old)} bars"
                continue
            note = "overlap missing; " if drift is None else f"overlap moved {drift:.2%} (re-adjusted); "
            log.warning("%s: %srefetching full history", sym, note)
        full = source.get_bars([sym], timeframe, None)[sym]
        if full.empty:  # never replace a good cache with nothing
            events[sym] = note + ("fetch returned no data; kept cache" if len(old) else "no data")
            continue
        save(full, bars_path(folder, sym))
        events[sym] = note + f"full fetch, {len(full)} bars"
    return events
