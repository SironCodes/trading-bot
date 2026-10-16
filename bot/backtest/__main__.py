"""python -m bot.backtest --start YYYY-MM-DD --end YYYY-MM-DD --universe tsx|crypto|all --sentiment on|off

Walk-forward backtest at 1x and 2x costs, with and without sentiment where archived news allows."""
import argparse
import logging
from dataclasses import replace

import pandas as pd

from bot.backtest import engine, report, walkforward
from bot.backtest.metrics import summarize
from bot.config import ROOT, load_settings
from bot.data import fx as fxmod
from bot.data.universe import load_bars, regime_bars

UNIVERSES = {"tsx": ["tsx"], "crypto": ["crypto"], "all": ["tsx", "crypto"]}
log = logging.getLogger("bot.backtest")


def ppy(cfg):
    return 252 if cfg.session else 365


def sleeve_metrics(settings, res, cfg) -> dict:
    return summarize(res.equity_cad, ppy(cfg), settings.backtest.risk_free_rate, res.trades, res.exposure,
                     res.fees_cad, res.slippage_cad, res.traded_cad)


def combined_metrics(settings, results) -> tuple[dict, pd.Series]:
    eq = engine.combined_cad(results)
    gross = pd.concat({n: r.exposure * r.equity_cad for n, r in results.items()}, axis=1, sort=True).ffill()
    exposure = engine.daily(gross.dropna().sum(axis=1)) / eq
    trades = pd.concat([r.trades for r in results.values()], ignore_index=True)
    m = summarize(eq, 365, settings.backtest.risk_free_rate, trades, exposure,
                  sum(r.fees_cad for r in results.values()), sum(r.slippage_cad for r in results.values()),
                  sum(r.traded_cad for r in results.values()))
    return m, eq


def benchmarks(settings, sleeves, start, end, rates) -> dict:
    """{asset class: {label: CAD equity}} for buy-and-hold of the proxy and equal-weight of the universe."""
    out = {}
    for sl in sleeves:
        times = engine.decision_times(sl.cfg, start, end)
        proxy = settings.regime.equity_proxy if sl.cfg.session else settings.regime.crypto_proxy
        out[sl.name] = {f"{proxy} buy & hold": engine.buy_and_hold(sl, [proxy], times, 1.0, rates),
                        "Equal-weight buy & hold": engine.buy_and_hold(sl, list(sl.bars), times, 1.0, rates)}
    return out


def run_variants(settings, sleeves, start, end, rates, rbars, news=None, sentiment_from=None) -> dict:
    return {m: engine.run(settings, sleeves, start, end, m, rates, rbars, news, sentiment_from)
            for m in settings.backtest.cost_multipliers}


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m bot.backtest", description=__doc__)
    p.add_argument("--start", required=True, type=pd.Timestamp)
    p.add_argument("--end", required=True, type=pd.Timestamp)
    p.add_argument("--universe", choices=list(UNIVERSES), default="all")
    p.add_argument("--sentiment", choices=["on", "off"], default="off")
    a = p.parse_args(argv)
    s = load_settings()
    logging.basicConfig(level=s.logging.level, format="%(levelname)s %(name)s: %(message)s")
    rates, rbars = fxmod.load(ROOT / s.paths.bars / "fx", s.data.fx.series), regime_bars(s)
    sleeves, dropped = [], {}
    for name in UNIVERSES[a.universe]:
        bars, d = load_bars(s, name)
        dropped |= d
        sleeves.append(engine.Sleeve(name, s.asset_classes[name], bars))
    first_bar = max(min(df.index[0] for df in sl.bars.values()) for sl in sleeves).tz_convert(None).normalize()
    start, end = max(a.start, first_bar), a.end
    notes = [f"Requested start {a.start.date()} moved to {start.date()}: first available bar."] if start > a.start else []

    schedules, folds = {}, []
    for sl in sleeves:
        log.info("walk-forward: %s", sl.name)
        schedules[sl.name], rows = walkforward.walk_forward(s, sl, start, end, rates, rbars)
        folds += rows
        if not schedules[sl.name]:
            raise SystemExit(f"{sl.name}: {start.date()}..{end.date()} is too short for one walk-forward fold")
    oos_start = max(sched[0][0] for sched in schedules.values())
    tuned = [replace(sl, schedule=schedules[sl.name]) for sl in sleeves]
    log.info("out-of-sample and full-period runs")
    variants = {f"OOS {m:g}x costs": r for m, r in run_variants(s, tuned, oos_start, end, rates, rbars).items()}
    variants |= {f"Full period {m:g}x costs, default params (in-sample)": r
                 for m, r in run_variants(s, sleeves, start, end, rates, rbars).items()}

    senti_lines, senti_variants = sentiment_section(a.sentiment, s, sleeves, tuned, oos_start, end, rates, rbars)
    variants |= senti_variants

    rows, trades, orders = [], [], []
    for label, results in variants.items():
        for sl in sleeves:
            if sl.name in results:
                rows.append({"asset_class": sl.name, "variant": label, **sleeve_metrics(s, results[sl.name], sl.cfg)})
                trades.append(results[sl.name].trades.assign(variant=label))
                orders.append(results[sl.name].orders.assign(variant=label))
        if len(sleeves) > 1 and len(results) == len(sleeves):
            rows.append({"asset_class": "combined", "variant": label, **combined_metrics(s, results)[0]})
    bench = {w: benchmarks(s, sleeves, w0, end, rates) for w, w0 in (("OOS", oos_start), ("Full period", start))}
    for window, per_class in bench.items():
        for name, curves in per_class.items():
            cfg = s.asset_classes[name]
            rows += [{"asset_class": name, "variant": f"{window}: {label}",
                      **summarize(eq, ppy(cfg), s.backtest.risk_free_rate)} for label, eq in curves.items()]
        if len(sleeves) > 1:
            for i, label in enumerate(["XIU.TO + BTC/USD buy & hold", "Equal-weight buy & hold (both)"]):
                eq = sum_daily(list(c.values())[i] for c in per_class.values())
                rows.append({"asset_class": "combined", "variant": f"{window}: {label}", **summarize(eq, 365)})

    out = ROOT / s.paths.reports / f"backtest_{a.universe}_sentiment-{a.sentiment}_{start.date()}_{end.date()}"
    charts = {}
    oos1 = variants[f"OOS {s.backtest.cost_multipliers[0]:g}x costs"]
    oos2 = variants[f"OOS {s.backtest.cost_multipliers[-1]:g}x costs"]
    for sl in sleeves:
        b = bench["OOS"][sl.name]
        charts[f"equity_drawdown_{sl.name}.png"] = (
            f"{sl.name.upper()}: out-of-sample {oos_start.date()} to {end.date()} (CAD)",
            {"Strategy, 1x costs": oos1[sl.name].equity_cad, "Strategy, 2x costs": oos2[sl.name].equity_cad, **b})
    if len(sleeves) > 1:
        b = [sum_daily(list(c.values())[i] for c in bench["OOS"].values()) for i in range(2)]
        charts["equity_drawdown_combined.png"] = (
            f"Combined CAD view: out-of-sample {oos_start.date()} to {end.date()}",
            {"Strategy, 1x costs": engine.combined_cad(oos1), "Strategy, 2x costs": engine.combined_cad(oos2),
             "XIU.TO + BTC/USD buy & hold": b[0], "Equal-weight buy & hold": b[1]})
    lines = body(s, a, sleeves, dropped, start, end, oos_start, folds, rows, notes, senti_lines, variants)
    path = report.write(out, lines, rows, charts, pd.concat(trades, ignore_index=True),
                        pd.concat(orders, ignore_index=True))
    print(f"report: {path}")


def sum_daily(curves) -> pd.Series:
    return engine.daily(pd.concat(list(curves), axis=1, sort=True).ffill().dropna().sum(axis=1))


def sentiment_section(flag, s, sleeves, tuned, oos_start, end, rates, rbars):
    """Sentiment-on vs -off runs, only on dates with archived news; otherwise say so."""
    if flag == "off":
        return ["Sentiment: off for this run (`--sentiment off`)."], {}
    from bot.sentiment.archive import coverage, load_index  # Phase 3 module; only needed with sentiment on

    news = load_index(s)
    lines, variants = ["## Sentiment (with vs without, covered dates only)", ""], {}
    for sl, tsl in zip(sleeves, tuned):
        cov = coverage(news, s, sl, oos_start, end)
        lines.append(f"- {sl.name}: {cov['text']}")
        if cov["from"] is None:
            continue
        for m in s.backtest.cost_multipliers:
            for on in (True, False):
                res = engine.run(s, [tsl], cov["from"], end, m, rates, rbars, news if on else None)
                variants[f"Sentiment {'ON' if on else 'OFF'}, {m:g}x costs, covered {cov['from'].date()}+"] = res
    return lines, variants


def verdicts(rows, sleeves, combined) -> list[str]:
    """Plain-language comparison of the headline (OOS, 1x) against buy-and-hold."""
    out = []
    names = [sl.name for sl in sleeves] + (["combined"] if combined else [])
    for name in names:
        mine = next((r for r in rows if r["asset_class"] == name and r["variant"] == "OOS 1x costs"), None)
        benches = [r for r in rows if r["asset_class"] == name and r["variant"].startswith("OOS: ")]
        if not mine or "cagr" not in mine:
            continue
        for b in benches:
            better = mine["cagr"] > b["cagr"]
            out.append(f"- {name}: strategy CAGR {mine['cagr']:+.1%} (Sharpe {mine['sharpe']:.2f}, max drawdown "
                       f"{mine['max_drawdown']:+.1%}) vs {b['variant'][5:]} {b['cagr']:+.1%} (Sharpe {b['sharpe']:.2f}, "
                       f"max drawdown {b['max_drawdown']:+.1%}): the strategy {'out' if better else 'UNDER'}performed.")
    return out


def body(s, a, sleeves, dropped, start, end, oos_start, folds, rows, notes, senti_lines, variants) -> list[str]:
    combined = len(sleeves) > 1
    fx_line = ("FX: crypto trades in USD; every CAD figure converts USD at the Bank of Canada daily USD/CAD rate "
               "of the business day before each valuation (no lookahead). No currency-conversion costs are modelled.")
    cost_lines = []
    for sl in sleeves:
        c = sl.cfg.costs
        fee = (f"commission {c.commission.per_share} CAD/share, min {c.commission.min_per_order} CAD" if c.commission
               else f"Alpaca fee {c.fee_bps[c.charge]:g} bps ({c.charge}) per fill")
        cost_lines.append(f"- {sl.name}: {fee}; slippage {c.slippage_bps:g} bps per fill; fills capped at "
                          f"{c.max_volume_participation:.0%} of the bar's volume; limit offsets "
                          f"{sl.cfg.orders.limit_offset_bps['entry']:g}/{sl.cfg.orders.limit_offset_bps['exit']:g} bps "
                          f"(entry/exit). 2x runs double fees and slippage.")
    order_stats = []
    for label, results in variants.items():
        if label.startswith("OOS"):
            for name, r in results.items():
                st = r.orders["status"] if len(r.orders) else pd.Series(dtype=str)
                order_stats.append(f"- {label}, {name}: {int(st.eq('filled').sum())} filled, "
                                   f"{int(st.str.startswith('partial').sum())} partial, "
                                   f"{int(st.str.startswith('expired').sum())} expired unfilled, "
                                   f"{int(st.str.startswith('rejected').sum())} rejected by risk checks")
    fold_rows = [{**f, "chosen_params": ", ".join(f"{k}={v}" for k, v in f["chosen_params"].items()),
                  "is_sharpe": f"{f['is_sharpe']:.2f}"} for f in folds]
    fold_table = ["| " + " | ".join(fold_rows[0]) + " |", "|" + "---|" * len(fold_rows[0])] + \
                 ["| " + " | ".join(str(v) for v in r.values()) + " |" for r in fold_rows]
    sections = []
    for name in [sl.name for sl in sleeves] + (["combined"] if combined else []):
        sel = [r for r in rows if r["asset_class"] == name]
        sections += [f"### {name}", "", report.table(sel, ["variant"]), ""]
    return [
        f"# Backtest: {a.universe}, sentiment {a.sentiment}", "",
        f"Period {start.date()} to {end.date()}; out-of-sample (walk-forward) from {oos_start.date()}. "
        f"Generated {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} UTC.", *notes, "",
        "## Headline (out-of-sample, 1x costs, vs buy-and-hold over the same window)", "",
        *verdicts(rows, sleeves, combined), "",
        "## Assumptions", "",
        "- Decisions use bars closed at or before the decision time and news available strictly before it; "
        "orders fill at the next bar's open plus slippage, only if marketable against the limit (no intrabar fills).",
        "- TSX decides 17:30 Toronto and fills next session's open; crypto decides 00:05 UTC and fills at the "
        "open of the UTC day that just began.",
        f"- {fx_line}", *cost_lines,
        "- Entries sized at 1% equity risk to a 3x ATR(14) stop, capped per symbol, by the regime/gross cap "
        "and by cash; positions are held until the trend exit or the stop (checked on closes).",
        "- Survivorship: the universe is today's large caps, which flatters strategy and benchmarks alike.",
        "- Regime macro-news term is 0 unless archived news covers the date.",
        *([f"- Dropped symbols: " + "; ".join(f"{k} ({v})" for k, v in dropped.items())] if dropped else []), "",
        "## Walk-forward folds (parameters chosen in-sample only)", "", *fold_table, "",
        *senti_lines, "",
        "## Results (CAD; per asset class and combined)", "",
        "Rows starting with OOS are the honest out-of-sample results. 'Full period ... (in-sample)' rows use "
        "the default parameters on the whole period and are shown for reference only.", "", *sections,
        "## Orders", "", *order_stats,
    ]


if __name__ == "__main__":
    main()
