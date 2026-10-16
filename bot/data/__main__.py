"""Refresh the bar and FX caches, then write reports/data_quality.{md,csv}.

Usage: python -m bot.data
"""
import logging
import sys

import pandas as pd

from bot.config import ROOT, load_class, load_settings
from bot.data import cache, fx, quality
from bot.data.universe import refresh_all


def main() -> None:
    s = load_settings()
    logging.basicConfig(level=s.logging.level, format="%(levelname)s %(name)s: %(message)s")
    events, now = refresh_all(s), pd.Timestamp.now(tz="UTC")
    header = [f"Generated {now:%Y-%m-%d %H:%M} UTC. Bars are stamped at their close time (UTC)."]
    rows, details = [], []
    for name, ac in s.asset_classes.items():
        described = sys.modules[load_class(ac.data).__module__].__doc__.splitlines()[0]
        window = f"last {ac.history_years:g}y" if ac.history_years else "all history"
        header.append(f"- {name}: {described} Full history cached; checks cover {window}; "
                      f"minimum usable history {ac.min_history_years:g}y.")
        for timeframe in sorted({st.timeframe for st in ac.strategies}):
            folder = ROOT / s.paths.bars / name / timeframe
            for sym in ac.symbols:
                row, lines = quality.check(sym, name, ac, cache.load(folder, sym), s.data.flag_daily_move,
                                           s.data.max_gap_days, events[sym], now)
                rows.append(row)
                details += lines
    header.append("- " + fx.summary(fx.load(ROOT / s.paths.bars / "fx", s.data.fx.series), s.data.fx.series))
    path = quality.write_report(rows, details, header, ROOT / s.paths.reports)
    print(quality.console_summary(rows))
    print("\n".join(header[1:]))
    print(f"report: {path}")


if __name__ == "__main__":
    main()
