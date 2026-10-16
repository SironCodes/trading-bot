"""Data-quality checks and the data-quality report.

Usable history is the stretch since the last hole longer than `max_gap_days` (before it the symbol
was not tradable, e.g. delisted); the minimum-history rule applies to it. The other checks run on the
analysis window (`history_years`, or all history when null). Missing/unexpected bars are measured
against the exchange calendar, so they double as the calendar cross-check.
"""
from pathlib import Path

import pandas as pd

from bot.market.calendar import expected_closes

FLAGS = ["missing_days", "unexpected_bars", "zero_volume", "nan_rows", "ohlc_violations", "big_moves"]
COLUMNS = ["asset_class", "symbol", "verdict", "years_available", "usable_years", "longest_gap_days",
           "first_bar", "last_bar", "window_start", "bars_in_window", *FLAGS, "median_value_1y", "notes", "cache"]
MAX_LISTED = 10  # detail items listed per symbol and flag
RUN_GAP_DAYS = 4  # display only: a weekend plus a holiday does not split a run of missing days
PRICE_EPS = 1e-6  # relative float tolerance: adjusted prices can leave high a hair below an equal close
LIQUIDITY_DAYS = 365  # calendar-day window for the median daily traded value column


def usable_start(df: pd.DataFrame, max_gap_days: int) -> pd.Timestamp:
    """First bar after the last hole longer than max_gap_days (the first bar if there is none)."""
    gaps = df.index.to_series().diff() > pd.Timedelta(days=max_gap_days)
    return df.index[gaps].max() if gaps.any() else df.index[0]


def check(symbol, asset_class, cfg, df, flag_move, max_gap_days, event, now) -> tuple[dict, list[str]]:
    """Return (report row, detail lines) for one symbol."""
    row = {"asset_class": asset_class, "symbol": symbol, "cache": event}
    if df.empty:
        return row | {"verdict": "DROP", "notes": "no data returned"}, []
    years = (df.index[-1] - df.index[0]).days / 365.25
    start = usable_start(df, max_gap_days)
    usable_years = (df.index[-1] - start).days / 365.25
    win = df if cfg.history_years is None else df[df.index >= now - pd.DateOffset(years=cfg.history_years)]
    expected = expected_closes(cfg, win.index[0], win.index[-1])
    missing, unexpected = expected.difference(win.index), win.index.difference(expected)
    moves = win["close"].pct_change()
    moves = moves[moves.abs() > flag_move]
    body = win[["open", "close"]]
    bad = ((win["high"] < body.max(axis=1) * (1 - PRICE_EPS)) | (win["low"] > body.min(axis=1) * (1 + PRICE_EPS))
           | (win[["open", "high", "low", "close"]] <= 0).any(axis=1))
    zero = win.index[win["volume"] == 0]
    last_year = df[df.index > df.index[-1] - pd.Timedelta(days=LIQUIDITY_DAYS)]
    row |= {
        "first_bar": df.index[0].date(), "last_bar": df.index[-1].date(), "years_available": round(years, 2),
        "usable_years": round(usable_years, 2), "longest_gap_days": df.index.to_series().diff().max().days,
        "window_start": win.index[0].date(), "bars_in_window": len(win),
        "missing_days": len(missing), "unexpected_bars": len(unexpected), "zero_volume": len(zero),
        "nan_rows": int(win.isna().any(axis=1).sum()), "ohlc_violations": int(bad.sum()), "big_moves": len(moves),
        "median_value_1y": round(float((last_year["close"] * last_year["volume"]).median())),
    }
    if usable_years < cfg.min_history_years:
        verdict, notes = "DROP", f"usable history {usable_years:.2f}y < {cfg.min_history_years:g}y minimum"
    else:
        flagged = [f for f in FLAGS if row[f]]
        verdict, notes = ("WARN", ", ".join(flagged)) if flagged else ("OK", "")
    details = []
    if start != df.index[0]:
        before = df.index[df.index < start][-1]
        details.append(f"{symbol} no bars from {(before + pd.Timedelta(days=1)).date()} to "
                       f"{(start - pd.Timedelta(days=1)).date()}; usable history restarts {start.date()}")
    if len(missing):
        details.append(f"{symbol} missing: " + _listing(_runs(missing)))
    if len(unexpected):
        details.append(f"{symbol} bars off the trading calendar: " + _listing([str(t.date()) for t in unexpected]))
    if len(zero):
        details.append(f"{symbol} zero volume: " + _listing([str(t.date()) for t in zero]))
    if row["ohlc_violations"]:
        details.append(f"{symbol} OHLC violations: " + _listing([str(t.date()) for t in win.index[bad]]))
    if len(moves):
        details.append(f"{symbol} moves > {flag_move:.0%}: " + _listing([f"{t.date()} {r:+.1%}" for t, r in moves.items()]))
    return row | {"verdict": verdict, "notes": notes}, details


def _runs(stamps: pd.DatetimeIndex) -> list[str]:
    runs = []
    for t in stamps:
        if runs and (t - runs[-1][1]).days <= RUN_GAP_DAYS:
            runs[-1][1], runs[-1][2] = t, runs[-1][2] + 1
        else:
            runs.append([t, t, 1])
    return [f"{a.date()}" if n == 1 else f"{a.date()} to {b.date()} ({n})" for a, b, n in runs]


def _listing(items: list[str]) -> str:
    more = f" ... and {len(items) - MAX_LISTED} more" if len(items) > MAX_LISTED else ""
    return ", ".join(items[:MAX_LISTED]) + more


def _markdown(df: pd.DataFrame) -> str:
    cells = df.astype(object).where(df.notna(), "").astype(str).values.tolist()
    lines = ["| " + " | ".join(df.columns) + " |", "|" + "---|" * len(df.columns)]
    return "\n".join(lines + ["| " + " | ".join(r) + " |" for r in cells])


def write_report(rows: list[dict], details: list[str], header: list[str], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    table = pd.DataFrame(rows).reindex(columns=COLUMNS)
    table.to_csv(out_dir / "data_quality.csv", index=False)
    dropped = [f"- {r.symbol}: {r.notes}" for r in table.itertuples() if r.verdict == "DROP"]
    lines = ["# Data quality report", "", *header, "", "## Per symbol", "", _markdown(table.drop(columns="cache")),
             "", "## Dropped", "", *(dropped or ["- none"]), "", "## Details", "",
             *([f"- {d}" for d in details] or ["- no issues found"]), "", "## Cache activity this run", "",
             *[f"- {r.symbol}: {r.cache}" for r in table.itertuples()]]
    path = out_dir / "data_quality.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def console_summary(rows: list[dict]) -> str:
    cols = ["symbol", "verdict", "years_available", "usable_years", "longest_gap_days", "missing_days",
            "unexpected_bars", "zero_volume", "ohlc_violations", "big_moves", "median_value_1y", "cache"]
    return pd.DataFrame(rows).reindex(columns=cols).to_string(index=False)
