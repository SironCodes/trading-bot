"""Performance metrics for an equity curve, its round-trip trades, exposure and costs."""
import math

import numpy as np
import pandas as pd


def summarize(equity: pd.Series, periods_per_year: int, risk_free_rate: float = 0.0, trades=None,
              exposure=None, fees=None, slippage=None, traded=None) -> dict:
    """Return the report's metrics. Money inputs (fees, slippage, traded) share the equity's currency."""
    eq = equity.dropna()
    nan = float("nan")
    if len(eq) < 3 or eq.iloc[0] <= 0:
        return {"total_return": nan}
    rets = eq.pct_change().dropna()
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1 / 365.25)
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0
    vol = rets.std(ddof=1) * math.sqrt(periods_per_year)
    excess = rets.mean() * periods_per_year - risk_free_rate
    downside = math.sqrt((np.minimum(rets, 0) ** 2).mean()) * math.sqrt(periods_per_year)
    mdd = (eq / eq.cummax() - 1).min()
    out = {"total_return": total, "cagr": cagr, "ann_vol": vol,
           "sharpe": excess / vol if vol > 0 else nan, "sortino": excess / downside if downside > 0 else nan,
           "max_drawdown": mdd, "calmar": cagr / abs(mdd) if mdd < 0 else nan}
    if trades is not None:
        pnl = trades["pnl_cad"] if len(trades) else pd.Series(dtype=float)
        wins, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
        out |= {"trades": len(pnl), "win_rate": (pnl > 0).mean() if len(pnl) else nan,
                "profit_factor": wins / losses if losses > 0 else (math.inf if wins > 0 else nan),
                "avg_holding_days": trades["holding_days"].mean() if len(pnl) else nan}
    if exposure is not None:
        out["avg_exposure"] = exposure.mean()
    if traded is not None:
        out["turnover"] = traded / eq.mean() / years
    if fees is not None:
        out["cost_drag"] = (fees + (slippage or 0.0)) / eq.mean() / years
    return out
