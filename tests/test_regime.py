import numpy as np
import pandas as pd

from bot.regime.macro import MacroRegime


def bars(values):
    idx = pd.date_range("2020-01-01", periods=len(values), freq="D", tz="UTC")
    return pd.DataFrame({"close": np.asarray(values, float)}, index=idx)


def calm(trend, n=400, seed=1):
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(trend + rng.normal(0, 0.005, n)))


def test_risk_on_neutral_off(settings):
    model = MacroRegime(settings.regime)
    up, down = bars(calm(0.002)), bars(calm(-0.002))
    assert model.classify({"XIU.TO": up, "BTC/USD": up}).label == "RISK_ON"  # +1 +1, calm
    assert model.classify({"XIU.TO": up, "BTC/USD": down}).label == "NEUTRAL"  # +1 -1
    off = model.classify({"XIU.TO": down, "BTC/USD": down})
    assert off.label == "RISK_OFF" and off.cap == 0.25 and "eq_trend -1" in off.reason


def test_volatility_spike_only_penalises(settings):
    model = MacroRegime(settings.regime)
    rng = np.random.default_rng(2)
    rets = np.r_[0.002 + rng.normal(0, 0.005, 380), 0.004 + rng.normal(0, 0.03, 20)]  # turbulent last month
    spike = bars(100 * np.exp(np.cumsum(rets)))
    state = model.classify({"XIU.TO": spike, "BTC/USD": bars(calm(0.002))})
    assert state.components["eq_vol"] == -1 and state.label == "NEUTRAL"


def test_macro_sentiment_and_missing_history(settings):
    model = MacroRegime(settings.regime)
    up = bars(calm(0.002))
    assert model.classify({"XIU.TO": up}, macro=(0.5, 10)).components == {"eq_trend": 1, "btc_trend": 0,
                                                                          "eq_vol": 0, "macro": 1}
    assert model.classify({"XIU.TO": up}, macro=(0.5, 2)).components["macro"] == 0  # too few articles
    assert model.classify({}).label == "NEUTRAL"  # nothing known: score 0
