import socket

import pandas as pd
import pytest

from bot.config import load_settings


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Unit tests must never reach a real API."""
    def refuse(*args, **kwargs):
        raise RuntimeError("network access is disabled in tests")
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture(scope="session")
def settings():
    return load_settings()


def daily_bars(n: int, start: str = "2024-01-01", scale: float = 1.0) -> pd.DataFrame:
    """n consecutive UTC-midnight daily bars with a steadily rising close."""
    idx = pd.date_range(start, periods=n, freq="D", tz="UTC", name="ts")
    close = pd.Series(range(100, 100 + n), index=idx, dtype="float64") * scale
    return pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1000.0})
