from datetime import datetime, timezone

import pandas as pd

from core.timeframes import align_higher_timeframe_data, filter_closed_candles


def frame(index, closes=None):
    closes = closes or list(range(1, len(index) + 1))
    return pd.DataFrame(
        {"open": closes, "high": [x + 1 for x in closes], "low": [x - 1 for x in closes],
         "close": closes, "volume": 1.0},
        index=pd.DatetimeIndex(index),
    )


def test_unfinished_1h_candle_excluded():
    data = frame(pd.date_range("2026-01-01 10:00", periods=2, freq="1h", tz="UTC"))
    result = filter_closed_candles(data, "1h", pd.Timestamp("2026-01-01 11:30", tz="UTC"))
    assert list(result.index.hour) == [10]


def test_unfinished_15m_candle_excluded():
    data = frame(pd.date_range("2026-01-01 10:00", periods=2, freq="15min", tz="UTC"))
    result = filter_closed_candles(data, "15m", pd.Timestamp("2026-01-01 10:29", tz="UTC"))
    assert list(result.index.minute) == [0]


def test_unfinished_5m_candle_excluded():
    data = frame(pd.date_range("2026-01-01 10:05", periods=2, freq="5min", tz="UTC"))
    result = filter_closed_candles(data, "5m", pd.Timestamp("2026-01-01 10:09:59", tz="UTC"))
    assert result.empty


def test_timezone_naive_input_is_treated_as_utc():
    data = frame(pd.date_range("2026-01-01 10:00", periods=1, freq="1h"))
    result = filter_closed_candles(data, "1h", datetime(2026, 1, 1, 11, tzinfo=timezone.utc))
    assert len(result) == 1
    assert str(result.index.tz) == "UTC"


def test_duplicates_are_removed_and_sorted():
    index = pd.DatetimeIndex([
        "2026-01-01 10:05+00:00", "2026-01-01 10:00+00:00", "2026-01-01 10:05+00:00"
    ])
    result = filter_closed_candles(frame(index, [2, 1, 3]), "5m", pd.Timestamp("2026-01-01 10:20", tz="UTC"))
    assert result.index.is_monotonic_increasing
    assert len(result) == 2
    assert result.iloc[-1]["close"] == 3


def test_higher_timeframe_alignment_waits_for_close():
    higher = frame(pd.date_range("2026-01-01 10:00", periods=2, freq="1h", tz="UTC"), [100, 999])
    lower = pd.date_range("2026-01-01 10:55", periods=3, freq="10min", tz="UTC")
    result = align_higher_timeframe_data(lower, higher, "1h")
    assert pd.isna(result.iloc[0]["close"])
    assert result.iloc[1]["close"] == 100
    assert result.iloc[2]["close"] == 100


def test_future_candle_value_cannot_change_earlier_closed_slice():
    base = frame(pd.date_range("2026-01-01 09:00", periods=3, freq="1h", tz="UTC"), [1, 2, 3])
    changed = base.copy()
    changed.loc[changed.index[-1], ["open", "high", "low", "close"]] = [99998, 100000, 99997, 99999]
    as_of = pd.Timestamp("2026-01-01 11:30", tz="UTC")
    pd.testing.assert_frame_equal(
        filter_closed_candles(base, "1h", as_of),
        filter_closed_candles(changed, "1h", as_of),
    )
