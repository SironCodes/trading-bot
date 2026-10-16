# trading-bot

A research-grade paper-trading and backtesting bot for TSX stocks/ETFs and crypto, in CAD.
Price/volume strategies drive every trade; news sentiment can only confirm, resize or veto them and
feeds a market-wide regime. It runs on paper. Live trading is locked and has never been enabled in
this build.

> Run it on paper for at least several weeks, and compare paper fills and P&L against the backtest
> at 2x costs, before you even consider live trading. Live fills will be worse than paper fills.

## Setup

Requires [uv](https://docs.astral.sh/uv/). The project is pinned to Python 3.12 (torch wheels).

```sh
cd trading-bot
uv sync                      # creates .venv with the pinned dependencies (uv.lock)
cp .env.example .env         # then put your Alpaca PAPER key id and secret in .env
```

`.env` is git-ignored. Keys are read only from the environment, are never logged (the log masks them),
and live keys (`ALPACA_LIVE_*`) are read only when live trading is fully unlocked.
Alpaca is used for crypto only: it does not list TSX securities. Historical crypto bars need no keys;
Alpaca News and the crypto paper account do.

## Daily use

| Command | What it does |
|---|---|
| `uv run python -m bot.data` | Refresh the Parquet bar cache (yfinance `.TO`, Alpaca crypto) and Bank of Canada USD/CAD; write `reports/data_quality.md` |
| `uv run python -m bot.backtest --start 2021-10-12 --end 2026-10-09 --universe all --sentiment off` | Walk-forward backtest; see below |
| `uv run python -m bot.sentiment` | Fetch news into the archive and score new headlines with FinBERT |
| `uv run python -m bot.runner --dry-run` | One live decision pass that logs intended orders and sends nothing |
| `uv run python -m bot.runner` | One paper pass: TSX to the SimulatedBroker, crypto to Alpaca paper |
| `uv run python -m bot.killswitch` | Cancel all open orders, flatten all positions, latch the bot |
| `uv run python -m pytest` | Tests (no network). Use `python -m pytest`: the `#` in the folder name breaks the `pytest` launcher |

### Scheduling

Each runner pass refreshes data, reconciles with the brokers, and runs every asset class's latest
decision slot once (TSX after the 16:00 close at 17:30 Toronto, crypto at 00:05 UTC), so it is safe to
run often. Example crontab:

```
*/15 * * * * cd "/path/to/trading-bot" && /path/to/uv run python -m bot.runner >> logs/cron.log 2>&1
0 * * * *    cd "/path/to/trading-bot" && /path/to/uv run python -m bot.sentiment >> logs/cron.log 2>&1
```

Schedule `bot.sentiment` early: free Canadian news has no deep history, so TSX sentiment only becomes
backtestable from the day the archive starts.

TSX orders placed after the close fill at the next session's open, which the SimulatedBroker records once
that bar has closed (the following pass after the next close). Replays for testing:
`uv run python -m bot.runner --as-of 2026-10-08T22:00Z --db state/demo.sqlite` (simulated brokers only,
cached data; always use a scratch `--db`).

### Where things are recorded

`state/paper.sqlite` (one file per environment): `cycles`, `signals`, `regime`, `decisions` (with a reason
for every symbol), `orders` (timestamp, symbol, side, qty, type, limit, time in force, client_order_id,
reason, status), `fills`, `equity_daily`, `stops`, `control` (kill latch) and the SimulatedBroker ledger
(`sim_orders`, `sim_fills`, `sim_cash`). `data/news.sqlite` holds every news document with the time it was
first seen, plus scores. `logs/bot.jsonl` is the structured log.

```sh
sqlite3 -header -column state/paper.sqlite "select symbol, action, reason from decisions order by rowid desc limit 18"
```

## Backtests

`python -m bot.backtest --start --end --universe tsx|crypto|all --sentiment on|off` writes
`reports/backtest_<universe>_sentiment-<on|off>_<start>_<end>/` with `report.md`, `metrics.json`,
equity/drawdown PNGs per asset class and combined, `trades.csv` (entry/exit reasons) and `orders.csv`.

How it stays honest:
- **No lookahead.** A decision at time t sees bars closed at or before t and news available to the bot
  strictly before t. Orders fill at the next bar's open plus slippage, only if marketable against their
  limit. The same `pipeline.decide`, risk checks and fill model are used live.
- **Out-of-sample first.** Parameters change only inside walk-forward (24-month in-sample, 6-month
  out-of-sample, rolling). Rows labelled `OOS` are the result; full-period rows are in-sample, for reference.
- **Costs at 1x and 2x.** TSX: CAD 0.01/share, min CAD 1.00, 10 bps slippage. Crypto: Alpaca's 25 bps taker
  fee on every fill, 15 bps slippage. Fills capped at 5% of the bar's volume.
- **Sentiment only where archived.** RSS news counts from the moment the bot first saw it; Alpaca News
  (vendor timestamps) is treated as point-in-time and labelled vendor-archived. Uncovered periods are
  reported as "sentiment not backtestable for this period", with coverage counts.
- **CAD everywhere.** Crypto USD values use the Bank of Canada rate of the business day before each
  valuation. No currency-conversion costs are modelled.
- Benchmarks: buy-and-hold XIU.TO, BTC/USD, and equal-weight buy-and-hold of each universe.

## Configuration (`config/settings.yaml`)

Every tunable lives here. ⚠ marks default risk limits: change them deliberately.

| Section | Holds |
|---|---|
| `mode` | `paper` or `live` (one of the three live switches) |
| `alpaca.endpoints` | The only place the paper/live trading endpoints are defined |
| `paths` | Bar cache, news archive, state DB (`{env}` keeps paper and live apart), log, reports |
| `data` | Incremental-update overlap and re-adjustment tolerance, outlier flag, `max_gap_days`, FX series |
| `asset_classes.<tsx/crypto>` | Currency, capital, history window and minimum, data source and broker classes, symbols, session, decision time, strategies (class, weight, timeframe, holding period, params, walk-forward grid, order type), walk-forward windows, order offsets and time in force, costs, venue rules, risk limits |
| `combined_risk` | Gross exposure and open-order caps across asset classes (CAD) |
| `fusion` | Sentiment veto threshold and minimum articles, scale-up cap |
| `sentiment` | Lookback, half-life, de-duplication, scorer (FinBERT), sources |
| `regime` | Proxies, windows, thresholds and the 100% / 60% / 25% gross caps |
| `backtest` | Cost multipliers, risk-free rate, benchmarks |
| `killswitch` | `order_type: market` (the explicit opt-in) |

The regime rule is documented verbatim in `bot/regime/macro.py`.

## Kill switch

`python -m bot.killswitch [--env paper|live] [--dry-run] [--reset]`
- Cancels every open order and flattens every position on every broker. Alpaca closes positions now
  with market orders. The TSX SimulatedBroker queues market sells that fill at the next session's open.
- Latches the bot: the runner opens no new positions (exits still run) until `--reset`.
- A broker that fails is reported (exit code 1) and never stops the others.

## Live trading lock

The bot refuses to start live unless ALL three are set: `--env live` (or `TRADING_ENV=live`), `mode: live`
in settings.yaml, and `ENABLE_LIVE_TRADING=yes`. It prints a warning whenever live is requested. TSX stays
simulated in every mode until a real Canadian broker adapter exists. Before going live: weeks of paper
results, a check that Alpaca offers crypto trading where you live, and your own review of every risk limit.

## Extending

- **Strategy:** add `bot/strategies/<name>.py` with a `Strategy` subclass (`lookback`, `generate_signals`)
  and an entry under an asset class's `strategies:` with `class: bot.strategies.<name>:<Class>`.
  Nothing else changes.
- **Sentiment source:** subclass `SentimentSource` in `bot/sentiment/sources/<name>.py` (`fetch(symbols,
  start, end) -> [Document]`) and list it under `sentiment.sources`. Set `point_in_time_history: true` only
  if the vendor's publish timestamps can be trusted for backfill. `RedditSource` is the stub to fill in.
- **Timeframe:** strategies declare `timeframe`; the cache stores bars per timeframe. v1 accepts `1D`
  only. Adding one means a data source that returns it, a config literal, and decision times for it in
  `bot/market/calendar.py`.
- **Real broker:** implement `Broker` (`bot/execution/brokers/base.py`) with the constructor
  `(settings, asset_class, env, conn)`, e.g. by completing the `CanadianBroker` stub for Questrade or
  Interactive Brokers, and point the asset class's `broker:` at it. Orders still go only through
  `OrderManager`. A test fails if research code imports execution code.
- **Scorer:** subclass `SentimentScorer`. Any LLM-based scorer must set `knowledge_cutoff`; backtests then
  ignore news published before it.

## Known limitations

- The SimulatedBroker does not credit dividends, while backtests use dividend-adjusted prices, so TSX
  paper P&L will trail backtests by roughly the dividend yield.
- No intrabar fills: a limit not marketable at the open expires. TSX early closes are treated as full days.
- The universe is today's large caps (survivorship bias flatters strategy and benchmarks alike).
- SOL/USD is auto-dropped: Alpaca has no bars for it from 2023-07-08 to 2024-08-26. It re-enters once its
  usable history reaches the 3-year minimum.
- Alpaca's crypto volume is far lower than in 2021-22, so the 5% fill cap binds more in recent years.
- yfinance is unofficial; it sits behind `MarketData` so it can be swapped.
- Walk-forward tunes only the first strategy of each asset class.

## Layout

```
config/settings.yaml   bot/config.py (schema, live lock)   bot/pipeline.py (the one decision path)
bot/data/              bars, FX, cache, quality report      bot/market/   calendar, tick/lot rules, costs, fills
bot/strategies/        Strategy + v1 SmaTrend               bot/fusion/   quant + sentiment + holdings -> action
bot/risk/              sizing and pre-trade limits          bot/regime/   macro regime and caps
bot/sentiment/         sources, FinBERT, archive, index     bot/backtest/ engine, walk-forward, metrics, report
bot/execution/         brokers, router, OrderManager        bot/runner/   scheduled passes
bot/killswitch.py      bot/storage/  SQLite + JSON logs     tests/        no-network unit tests
```
