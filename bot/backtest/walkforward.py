"""Walk-forward evaluation: parameters are chosen on an in-sample window and only then applied to the
following out-of-sample window. This is the ONLY place strategy parameters may change."""
import itertools
import math
from dataclasses import replace

import pandas as pd

from bot.backtest import engine
from bot.backtest.metrics import summarize
from bot.pipeline import make_context

PERIODS = {True: 252, False: 365}  # periods per year: session-based market vs 24/7


def folds(start, end, wf) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    """Rolling (in-sample start, in-sample end = out-of-sample start, out-of-sample end) windows."""
    out, s, end = [], pd.Timestamp(start), pd.Timestamp(end)
    while (is_end := s + pd.DateOffset(months=wf.in_sample_months)) < end:
        out.append((s, is_end, min(is_end + pd.DateOffset(months=wf.out_of_sample_months), end)))
        s += pd.DateOffset(months=wf.out_of_sample_months)
    return out


def grid(cfg) -> list[dict]:
    """Parameter grid of the asset class's first strategy, config defaults first (ties keep them).
    shortcut: one strategy per asset class is tuned; tune each separately once there are several."""
    st = cfg.strategies[0]
    keys = list(st.walk_forward_grid)
    default = {k: st.params[k] for k in keys}
    combos = [dict(zip(keys, v)) for v in itertools.product(*(st.walk_forward_grid[k] for k in keys))]
    return [default] + [c for c in combos if c != default]


def warm_start(sleeve, settings, candidates) -> pd.Timestamp:
    """First moment every grid variant has enough history on every symbol: in-sample scores start here."""
    need = max(make_context(sleeve.name, sleeve.cfg, settings.fusion, p).strategies[0].lookback for p in candidates)
    return max(df.index[min(need, len(df) - 1)] for df in sleeve.bars.values())


def optimize(settings, sleeve, fold, fx, regime_bars):
    """Pick the in-sample Sharpe-maximising parameters (1x costs, no sentiment). Returns (params, rows)."""
    is_start, is_end, _ = fold
    candidates = grid(sleeve.cfg)
    warm, best, best_sharpe, rows = warm_start(sleeve, settings, candidates), candidates[0], -math.inf, []
    for params in candidates:
        res = engine.run(settings, [replace(sleeve, schedule=[(is_start, params)])], is_start,
                         is_end - pd.Timedelta(days=1), 1.0, fx, regime_bars)[sleeve.name]
        eq = res.equity[res.equity.index >= warm]
        sharpe = summarize(eq, PERIODS[sleeve.cfg.session is not None]).get("sharpe", math.nan)
        rows.append({"params": params, "is_sharpe": sharpe})
        if sharpe > best_sharpe:  # strict: NaN never wins, ties keep the earlier (default-first) params
            best, best_sharpe = params, sharpe
    return best, rows


def walk_forward(settings, sleeve, start, end, fx, regime_bars):
    """Return (schedule for the out-of-sample run, per-fold summary rows)."""
    schedule, rows = [], []
    for fold in folds(start, end, sleeve.cfg.walk_forward):
        params, tried = optimize(settings, sleeve, fold, fx, regime_bars)
        schedule.append((fold[1], params))
        best = max((r["is_sharpe"] for r in tried if not math.isnan(r["is_sharpe"])), default=math.nan)
        rows.append({"asset_class": sleeve.name, "in_sample": f"{fold[0].date()} to {fold[1].date()}",
                     "out_of_sample": f"{fold[1].date()} to {fold[2].date()}", "chosen_params": params,
                     "is_sharpe": best})
    return schedule, rows
