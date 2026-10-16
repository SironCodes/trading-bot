"""Backtest report: report.md, metrics.json, equity/drawdown PNGs, trades.csv and orders.csv."""
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

# Reference light palette (dataviz skill): chart chrome + categorical slots 1-4 in their fixed order.
SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
LABEL_GAP = 0.035  # minimum vertical gap between end labels, as a share of the y-range
PCT = {"total_return", "cagr", "ann_vol", "max_drawdown", "win_rate", "avg_exposure", "cost_drag"}
COLUMNS = ["total_return", "cagr", "ann_vol", "sharpe", "sortino", "max_drawdown", "calmar", "win_rate",
           "profit_factor", "avg_holding_days", "turnover", "avg_exposure", "trades", "cost_drag"]


def chart(path: Path, title: str, curves: dict) -> None:
    """Equity (indexed to 100) over drawdown: two panels, one y-axis each; <= 4 series."""
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(10, 6.4), sharex=True, height_ratios=[3, 1.3],
                                      facecolor=SURFACE)
    ends = []
    for (name, eq), color in zip(curves.items(), SERIES):
        eq = eq.dropna()
        style = "--" if "2x" in name else "-"
        idx = eq / eq.iloc[0] * 100
        top.plot(idx.index, idx, color=color, lw=1.6, ls=style, label=name)
        ends.append([idx.iloc[-1], idx.index[-1]])
        bottom.plot(eq.index, (eq / eq.cummax() - 1) * 100, color=color, lw=1.2, ls=style)
    lo, hi = top.get_ylim()
    gap, placed = (hi - lo) * LABEL_GAP, []
    for value, x in sorted(ends):  # nudge end labels apart so close finishes stay readable
        y = max(value, placed[-1] + gap) if placed else value
        placed.append(y)
        top.annotate(f"{value:.0f}", (x, y), xytext=(4, 0), textcoords="offset points", color=INK2, fontsize=8,
                     va="center")
    for ax in (top, bottom):
        ax.set_facecolor(SURFACE)
        ax.grid(True, color=GRID, lw=0.6)
        ax.spines[["top", "right"]].set_visible(False)
        ax.spines[["left", "bottom"]].set_color(AXIS)
        ax.tick_params(colors=MUTED, labelsize=8)
    top.set_title(title, color=INK, loc="left", fontsize=11)
    top.set_ylabel("Equity, start = 100 (CAD)", color=INK2, fontsize=9)
    bottom.set_ylabel("Drawdown %", color=INK2, fontsize=9)
    top.legend(frameon=False, labelcolor=INK2, fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)


def _fmt(key, value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    if key in PCT:
        return f"{value:+.1%}" if key in ("total_return", "cagr", "max_drawdown") else f"{value:.1%}"
    if key == "trades":
        return str(int(value))
    return "inf" if value == math.inf else f"{value:.2f}"


def table(rows: list[dict], first: list[str]) -> str:
    cols = first + [c for c in COLUMNS if any(c in r for r in rows)]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "")) if c in first else _fmt(c, r.get(c)) for c in cols) + " |")
    return "\n".join(lines)


def write(out_dir: Path, lines: list[str], rows: list[dict], charts: dict, trades: pd.DataFrame,
          orders: pd.DataFrame) -> Path:
    """`lines` is the markdown body; charts maps file name -> (title, curves)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, (title, curves) in charts.items():
        chart(out_dir / name, title, curves)
    trades.to_csv(out_dir / "trades.csv", index=False)
    orders.to_csv(out_dir / "orders.csv", index=False)
    (out_dir / "metrics.json").write_text(json.dumps(rows, indent=2, default=str))
    path = out_dir / "report.md"
    path.write_text("\n".join(lines + ["", "## Files", ""] + [f"- ![{n}]({n})" for n in charts]
                              + ["- trades.csv: every round trip with entry/exit reasons (all variants)",
                                 "- orders.csv: every order incl. expired and risk-rejected ones",
                                 "- metrics.json: all metrics above, machine-readable"]) + "\n")
    return path
