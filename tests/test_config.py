import copy

import pytest
import yaml
from pydantic import ValidationError

from bot.config import SETTINGS_PATH, LiveTradingLocked, Settings, resolve_env

RAW = yaml.safe_load(SETTINGS_PATH.read_text())


def test_settings_file_holds_the_approved_defaults(settings):
    tsx, crypto = settings.asset_classes["tsx"], settings.asset_classes["crypto"]
    assert settings.mode == "paper"
    assert settings.alpaca.endpoints.paper == "https://paper-api.alpaca.markets"
    assert (tsx.min_history_years, crypto.min_history_years, crypto.history_years) == (5, 3, None)
    assert tsx.rules.lot_policy == "whole_shares"
    assert (tsx.costs.commission.per_share, tsx.costs.commission.min_per_order) == (0.01, 1.00)
    assert crypto.costs.fee_bps == {"maker": 15, "taker": 25} and crypto.costs.charge == "taker"
    assert tsx.risk.max_position.pct_equity == 0.10 and crypto.risk.max_position.pct_equity == 0.40
    assert settings.regime.gross_caps == {"RISK_ON": 1.0, "NEUTRAL": 0.6, "RISK_OFF": 0.25}
    assert settings.killswitch.order_type == "market"


def _with(path, value):
    raw = copy.deepcopy(RAW)
    node = raw
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return raw


@pytest.mark.parametrize("path,value", [
    (("asset_classes", "tsx", "risk", "max_positon"), 3),  # typo'd key
    (("asset_classes", "tsx", "risk", "max_position"), {"pct_equity": None, "cad": None}),
    (("asset_classes", "tsx", "risk", "max_daily_loss"), 1.5),
    (("alpaca", "endpoints", "paper"), "https://api.alpaca.markets"),
    (("asset_classes", "crypto", "strategies", 0, "timeframe"), "1H"),
    (("mode",), "yolo"),
])
def test_invalid_settings_are_rejected(path, value):
    with pytest.raises(ValidationError):
        Settings.model_validate(_with(path, value))


# The live lock is a pure function over a plain dict: these tests never touch os.environ.

@pytest.mark.parametrize("cli,mode,environ,expected", [
    (None, "paper", {}, "paper"),
    (None, "live", {"ENABLE_LIVE_TRADING": "yes"}, "paper"),  # live never requested
    ("paper", "live", {"TRADING_ENV": "live", "ENABLE_LIVE_TRADING": "yes"}, "paper"),  # explicit flag wins
    ("live", "live", {"ENABLE_LIVE_TRADING": "yes"}, "live"),
    (None, "live", {"TRADING_ENV": "live", "ENABLE_LIVE_TRADING": "yes"}, "live"),
])
def test_environment_resolution(cli, mode, environ, expected):
    assert resolve_env(cli, mode, environ) == expected


@pytest.mark.parametrize("cli,mode,environ", [
    ("live", "paper", {"ENABLE_LIVE_TRADING": "yes"}),  # config still says paper
    ("live", "live", {}),  # flag missing
    ("live", "live", {"ENABLE_LIVE_TRADING": "YES"}),  # must be exactly "yes"
    ("live", "live", {"ENABLE_LIVE_TRADING": "true"}),
    (None, "paper", {"TRADING_ENV": "live"}),
])
def test_live_is_refused_unless_all_three_switches_are_set(cli, mode, environ, capsys):
    with pytest.raises(LiveTradingLocked):
        resolve_env(cli, mode, environ)
    assert "LIVE TRADING REQUESTED" in capsys.readouterr().err


def test_unknown_environment_is_rejected():
    with pytest.raises(ValueError):
        resolve_env("staging", "paper", {})
