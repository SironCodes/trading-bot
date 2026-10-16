"""USD/CAD daily rates from the Bank of Canada Valet API (official series FXUSDCAD, from 2017-01-03)."""
import json
import logging
import urllib.request
from pathlib import Path

import pandas as pd

from bot.data.cache import save

log = logging.getLogger(__name__)
VALET = "https://www.bankofcanada.ca/valet/observations/{series}/json"
TIMEOUT_S = 30
PUBLISH_TZ = "America/Toronto"  # the Bank of Canada publishes on Ottawa (Eastern) business days


def rate_at(rates: pd.Series, as_of: pd.Timestamp) -> float:
    """CAD per USD from the latest Bank of Canada business day strictly BEFORE as_of's Eastern date.
    A day's rate is only published that afternoon, so using the prior day's rate avoids lookahead."""
    day = as_of.tz_convert(PUBLISH_TZ).tz_localize(None).normalize()
    i = rates.index.searchsorted(day, side="left") - 1
    if i < 0:
        raise ValueError(f"no USD/CAD rate published before {day.date()}")
    return float(rates.iloc[i])


def parse(payload: dict, series: str) -> pd.Series:
    obs = {o["d"]: float(o[series]["v"]) for o in payload["observations"] if o.get(series, {}).get("v")}
    rates = pd.Series(obs, name=series, dtype="float64")
    rates.index = pd.DatetimeIndex(rates.index, name="date")
    return rates.sort_index()


def load(folder: Path, series: str) -> pd.Series:
    path = Path(folder) / f"{series}.parquet"
    return pd.read_parquet(path)[series] if path.exists() else pd.Series(name=series, dtype="float64")


def refresh(folder: Path, series: str) -> pd.Series:
    """Download the whole (small) series and cache it; on failure keep and return the cached copy."""
    try:
        with urllib.request.urlopen(VALET.format(series=series), timeout=TIMEOUT_S) as resp:
            rates = parse(json.load(resp), series)
    except (OSError, ValueError, KeyError) as e:
        log.warning("Bank of Canada %s download failed (%s); using cache", series, e)
        return load(folder, series)
    save(rates.to_frame(), Path(folder) / f"{series}.parquet")
    return rates


def summary(rates: pd.Series, series: str) -> str:
    if rates.empty:
        return f"USD/CAD ({series}): no data"
    gap = rates.index.to_series().diff().max().days
    return (f"USD/CAD (Bank of Canada {series}): {rates.index[0].date()} -> {rates.index[-1].date()}, "
            f"{len(rates)} business days, longest gap {gap} calendar days, latest {rates.iloc[-1]:.4f}")
