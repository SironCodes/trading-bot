from conftest import daily_bars

from bot.data import cache
from bot.data.base import MarketData


class FakeSource(MarketData):
    def __init__(self, frame):
        super().__init__(cfg=None)
        self.frame, self.starts = frame, []

    def get_bars(self, symbols, timeframe, start, end=None):
        self.starts.append(start)
        frame = self.frame if start is None else self.frame[self.frame.index.date >= start]
        return {symbols[0]: frame}


def refresh(source, folder):
    return cache.refresh(source, folder, ["X"], "1D", overlap_bars=10, tolerance=0.0005)["X"]


def test_first_refresh_fetches_full_history(tmp_path):
    src = FakeSource(daily_bars(30))
    assert refresh(src, tmp_path) == "full fetch, 30 bars"
    assert src.starts == [None] and len(cache.load(tmp_path, "X")) == 30


def test_update_appends_only_new_bars_after_rereading_the_overlap(tmp_path):
    src = FakeSource(daily_bars(30))
    refresh(src, tmp_path)
    src.frame = daily_bars(35)
    assert refresh(src, tmp_path) == "+5 bars"
    assert src.starts[-1] is not None  # incremental request, not a full refetch
    assert cache.load(tmp_path, "X").equals(daily_bars(35))


def test_readjusted_history_triggers_a_full_refetch(tmp_path):
    src = FakeSource(daily_bars(30))
    refresh(src, tmp_path)
    src.frame = daily_bars(31, scale=0.98)  # e.g. a dividend adjustment rescales all past prices
    event = refresh(src, tmp_path)
    assert "re-adjusted" in event and src.starts[-1] is None
    assert cache.load(tmp_path, "X").equals(daily_bars(31, scale=0.98))


def test_a_failed_fetch_never_wipes_the_cache(tmp_path):
    src = FakeSource(daily_bars(30))
    refresh(src, tmp_path)
    src.frame = daily_bars(0)
    assert "kept cache" in refresh(src, tmp_path)
    assert len(cache.load(tmp_path, "X")) == 30
