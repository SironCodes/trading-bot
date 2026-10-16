import pandas as pd

from bot.data import crypto_alpaca, fx, tsx_yfinance

OHLCV = {"open": [1.0, 2.0, 3.0], "high": [1.5, 2.5, 3.5], "low": [0.5, 1.5, 2.5], "close": [1.2, 2.2, 3.2],
         "volume": [10.0, 20.0, 30.0]}


def test_yfinance_bars_are_stamped_at_the_tsx_close_and_unclosed_bars_dropped(settings, monkeypatch):
    idx = pd.DatetimeIndex(["2024-07-02", "2024-12-02", "2099-01-02"]).tz_localize("America/Toronto")
    raw = pd.DataFrame({k.capitalize(): v for k, v in OHLCV.items()}, index=idx)

    class FakeTicker:
        def __init__(self, symbol):
            pass

        def history(self, **kwargs):
            return raw

    monkeypatch.setattr(tsx_yfinance.yf, "Ticker", FakeTicker)
    bars = tsx_yfinance.YFinanceTSX(settings.asset_classes["tsx"]).get_bars(["RY.TO"], "1D", None)["RY.TO"]
    assert list(bars.columns) == ["open", "high", "low", "close", "volume"]
    assert list(bars.index) == [pd.Timestamp("2024-07-02 20:00", tz="UTC"), pd.Timestamp("2024-12-02 21:00", tz="UTC")]


def test_alpaca_bars_are_stamped_at_their_close_and_unclosed_bars_dropped(settings, monkeypatch):
    stamps = pd.DatetimeIndex(["2024-03-01", "2024-03-02", "2099-01-01"], tz="UTC")
    raw = pd.DataFrame(OHLCV | {"trade_count": [1, 2, 3], "vwap": [1.1, 2.1, 3.1]},
                       index=pd.MultiIndex.from_product([["BTC/USD"], stamps], names=["symbol", "timestamp"]))

    class FakeClient:
        def get_crypto_bars(self, request):
            return type("BarSet", (), {"df": raw})()

    monkeypatch.setattr(crypto_alpaca, "CryptoHistoricalDataClient", FakeClient)
    bars = crypto_alpaca.AlpacaCrypto(settings.asset_classes["crypto"]).get_bars(["BTC/USD", "ETH/USD"], "1D", None)
    assert list(bars["BTC/USD"].index) == [pd.Timestamp("2024-03-02", tz="UTC"), pd.Timestamp("2024-03-03", tz="UTC")]
    assert bars["ETH/USD"].empty


def test_bank_of_canada_payload_parses_to_a_dated_series():
    payload = {"observations": [{"d": "2026-10-08", "FXUSDCAD": {"v": "1.4240"}},
                                {"d": "2026-10-07", "FXUSDCAD": {"v": "1.4257"}}, {"d": "2026-10-06"}]}
    rates = fx.parse(payload, "FXUSDCAD")
    assert rates.to_dict() == {pd.Timestamp("2026-10-07"): 1.4257, pd.Timestamp("2026-10-08"): 1.4240}
