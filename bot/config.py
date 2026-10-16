"""Settings: config/settings.yaml -> validated pydantic models, plus the paper/live switch.

Every tunable lives in settings.yaml; modules read it from here and hold no magic numbers.
"""
from __future__ import annotations

import datetime as dt
import importlib
import sys
from pathlib import Path
from typing import Annotated, Any, Literal, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "config" / "settings.yaml"

Fraction = Annotated[float, Field(gt=0, le=1)]
Positive = Annotated[float, Field(gt=0)]
NonNeg = Annotated[float, Field(ge=0)]
PosInt = Annotated[int, Field(gt=0)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Endpoints(_Model):
    paper: str
    live: str

    @model_validator(mode="after")
    def _paper_points_at_paper(self):
        # A typo here must never send "paper" traffic to the live API.
        if not self.paper.startswith("https://paper-api."):
            raise ValueError("alpaca.endpoints.paper must be the paper-api host")
        return self


class Alpaca(_Model):
    endpoints: Endpoints


class Paths(_Model):
    bars: str
    news_db: str
    db: str
    logs: str
    reports: str


class Fx(_Model):
    source: Literal["bank_of_canada"]
    series: str


class Data(_Model):
    overlap_bars: PosInt
    adjustment_tolerance: Positive
    flag_daily_move: Positive
    max_gap_days: PosInt
    fx: Fx


class Session(_Model):
    open: dt.time
    close: dt.time
    tz: str


class Clock(_Model):
    time: dt.time
    tz: str


class StrategyCfg(_Model):
    class_path: str = Field(alias="class")
    weight: Positive
    timeframe: Literal["1D"]  # v1 runs on daily bars only
    holding_period_days: tuple[PosInt, PosInt]
    params: dict[str, Any]
    walk_forward_grid: dict[str, list[Any]]
    order_type: Literal["limit", "market"]


class WalkForward(_Model):
    in_sample_months: PosInt
    out_of_sample_months: PosInt
    objective: Literal["sharpe"]


class Orders(_Model):
    time_in_force: Literal["day", "gtc", "ioc"]
    limit_offset_bps: dict[Literal["entry", "exit"], NonNeg]


class Commission(_Model):
    per_share: NonNeg
    min_per_order: NonNeg


class Costs(_Model):
    commission: Commission | None = None
    fee_bps: dict[Literal["maker", "taker"], NonNeg] | None = None
    charge: Literal["maker", "taker"] | None = None
    slippage_bps: NonNeg
    max_volume_participation: Fraction


class PriceStep(_Model):
    from_price: NonNeg
    size: Positive


class Rules(_Model):
    tick: list[PriceStep] | None = None  # limit-price grid (TSX)
    board_lot: list[PriceStep] | None = None
    lot_policy: Literal["whole_shares", "board_lots"] | None = None
    qty_step: Positive | None = None  # fractional assets: order-size increment used by the backtester


class MaxPosition(_Model):
    pct_equity: Fraction | None = None
    cad: Positive | None = None

    @model_validator(mode="after")
    def _at_least_one(self):
        if self.pct_equity is None and self.cad is None:
            raise ValueError("max_position needs pct_equity, cad, or both")
        return self


class Risk(_Model):
    risk_per_trade: Fraction
    atr_period: PosInt
    stop_atr_multiple: Positive
    max_position: MaxPosition
    max_positions: PosInt
    max_gross_exposure: Fraction
    max_open_orders: PosInt
    max_daily_loss: Fraction
    min_order_cad: NonNeg
    rebalance_band: Annotated[float, Field(ge=0, lt=1)]


class AssetClass(_Model):
    currency: Literal["CAD", "USD"]
    capital_cad: Positive
    history_years: Positive | None  # None = all available history
    min_history_years: Positive
    data: str
    broker: str
    symbols: Annotated[list[str], Field(min_length=1)]
    session: Session | None = None  # None = trades 24/7
    decide_at: Clock
    strategies: Annotated[list[StrategyCfg], Field(min_length=1)]
    walk_forward: WalkForward
    orders: Orders
    costs: Costs
    rules: Rules
    risk: Risk


class CombinedRisk(_Model):
    max_gross_exposure: Fraction
    max_open_orders: PosInt


class Fusion(_Model):
    sentiment_veto_below: Annotated[float, Field(ge=-1, le=1)]
    sentiment_veto_min_articles: PosInt
    sentiment_scale_max: Annotated[float, Field(ge=1)]
    sentiment_scale_full_at: Fraction
    sentiment_min_articles: PosInt


class Scorer(_Model):
    class_path: str = Field(alias="class")
    model: str
    device: str
    batch_size: PosInt


class Sentiment(_Model):
    enabled: bool
    lookback_hours: Positive
    half_life_hours: Positive
    dedupe_jaccard: Fraction
    scorer: Scorer
    sources: list[dict[str, Any]]  # each source validates its own options


class MacroSentiment(_Model):
    bullish_at: float
    bearish_at: float
    min_articles: PosInt
    lookback_hours: Positive


class Regime(_Model):
    equity_proxy: str
    crypto_proxy: str
    trend_sma: PosInt
    vol_window: PosInt
    vol_median_window: PosInt
    vol_elevated_ratio: Positive
    macro: MacroSentiment
    risk_on_at: int
    risk_off_at: int
    gross_caps: dict[Literal["RISK_ON", "NEUTRAL", "RISK_OFF"], Fraction]


class Backtest(_Model):
    cost_multipliers: Annotated[list[Positive], Field(min_length=1)]
    risk_free_rate: float
    benchmarks: list[str]


class KillSwitch(_Model):
    order_type: Literal["market"]  # the explicit opt-in: a kill switch must flatten as fast as possible


class Logging(_Model):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(_Model):
    mode: Literal["paper", "live"]
    base_currency: Literal["CAD"]
    alpaca: Alpaca
    paths: Paths
    data: Data
    asset_classes: dict[str, AssetClass]
    combined_risk: CombinedRisk
    fusion: Fusion
    sentiment: Sentiment
    regime: Regime
    backtest: Backtest
    killswitch: KillSwitch
    logging: Logging


def load_settings(path: Path = SETTINGS_PATH) -> Settings:
    return Settings.model_validate(yaml.safe_load(Path(path).read_text()))


def load_class(path: str):
    """Import 'package.module:ClassName': how config names data sources, strategies and brokers."""
    module, _, name = path.partition(":")
    return getattr(importlib.import_module(module), name)


# --- paper / live switch ---------------------------------------------------------------------

LIVE_FLAG = "ENABLE_LIVE_TRADING"


class LiveTradingLocked(RuntimeError):
    pass


def resolve_env(cli_env: str | None, mode: str, environ: Mapping[str, str]) -> str:
    """Return 'paper' or 'live'. Live needs ALL of: --env live (or TRADING_ENV=live),
    `mode: live` in settings.yaml, and ENABLE_LIVE_TRADING=yes; otherwise refuse to start."""
    requested = cli_env or environ.get("TRADING_ENV") or "paper"
    if requested == "paper":
        return "paper"
    if requested != "live":
        raise ValueError(f"unknown environment {requested!r}; use paper or live")
    bar = "!" * 64
    print(f"\n{bar}\n!!   LIVE TRADING REQUESTED - REAL MONEY WOULD BE AT RISK   !!\n{bar}\n", file=sys.stderr)
    missing = [] if mode == "live" else ["`mode: live` in config/settings.yaml"]
    if environ.get(LIVE_FLAG) != "yes":
        missing.append(f"{LIVE_FLAG}=yes")
    if missing:
        raise LiveTradingLocked("refusing to start live trading; missing: " + ", ".join(missing))
    return "live"


class MissingCredentials(RuntimeError):
    pass


def alpaca_endpoint(settings: Settings, env: str) -> str:
    """The one place code reads the trading endpoint for the resolved environment."""
    return getattr(settings.alpaca.endpoints, env)


def alpaca_credentials(env: str, environ: Mapping[str, str]) -> tuple[str, SecretStr]:
    """(key id, secret) for the resolved environment, read only from environment variables."""
    prefix = f"ALPACA_{env.upper()}_"
    key, secret = environ.get(prefix + "KEY"), environ.get(prefix + "SECRET")
    if not key or not secret:
        raise MissingCredentials(f"{prefix}KEY / {prefix}SECRET are not set (copy .env.example to .env)")
    return key, SecretStr(secret)
