"""Refresh and load cached bars for the bot, applying the data-quality eligibility rule ("drop and report")."""
import pandas as pd

from bot.config import ROOT, load_class
from bot.data import cache, fx
from bot.data.quality import usable_start


def refresh_all(settings) -> dict[str, str]:
    """Bring every asset class's bar cache and the USD/CAD series up to date; one event per symbol."""
    events = {}
    for name, ac in settings.asset_classes.items():
        source = load_class(ac.data)(ac)
        for timeframe in sorted({st.timeframe for st in ac.strategies}):
            events |= cache.refresh(source, ROOT / settings.paths.bars / name / timeframe, ac.symbols, timeframe,
                                    settings.data.overlap_bars, settings.data.adjustment_tolerance)
    fx.refresh(ROOT / settings.paths.bars / "fx", settings.data.fx.series)
    return events


def _folder(settings, name):
    return ROOT / settings.paths.bars / name / settings.asset_classes[name].strategies[0].timeframe


def load_bars(settings, name) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """(bars for eligible symbols, cut to their usable history; {dropped symbol: reason})."""
    ac, bars, dropped = settings.asset_classes[name], {}, {}
    for sym in ac.symbols:
        df = cache.load(_folder(settings, name), sym)
        if df.empty:
            dropped[sym] = "no cached data (run python -m bot.data)"
            continue
        start = usable_start(df, settings.data.max_gap_days)
        years = (df.index[-1] - start).days / 365.25
        if years < ac.min_history_years:
            dropped[sym] = f"usable history {years:.2f}y < {ac.min_history_years:g}y minimum (since {start.date()})"
            continue
        bars[sym] = df[df.index >= start]
    return bars, dropped


def regime_bars(settings) -> dict[str, pd.DataFrame]:
    """The regime's proxies (XIU.TO, BTC/USD), whether or not they are traded."""
    out = {}
    for proxy in (settings.regime.equity_proxy, settings.regime.crypto_proxy):
        name = next(n for n, ac in settings.asset_classes.items() if proxy in ac.symbols)
        out[proxy] = cache.load(_folder(settings, name), proxy)
    return out
