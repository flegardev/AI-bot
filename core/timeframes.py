from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


def timeframe_to_timedelta(timeframe: str) -> timedelta:
    value = timeframe.strip().lower()
    if len(value) < 2 or value[-1] not in _UNITS:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    try:
        amount = int(value[:-1])
    except ValueError as exc:
        raise ValueError(f"Unsupported timeframe: {timeframe}") from exc
    if amount <= 0:
        raise ValueError("Timeframe must be positive")
    return timedelta(**{_UNITS[value[-1]]: amount})


def _utc(value: datetime | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def candle_close_time(open_time: datetime | pd.Timestamp, timeframe: str) -> pd.Timestamp:
    return _utc(open_time) + timeframe_to_timedelta(timeframe)


def is_candle_closed(
    open_time: datetime | pd.Timestamp,
    timeframe: str,
    as_of: datetime | pd.Timestamp | None = None,
) -> bool:
    observed_at = _utc(as_of or datetime.now(timezone.utc))
    return candle_close_time(open_time, timeframe) <= observed_at


def normalize_candles(df: pd.DataFrame) -> pd.DataFrame:
    required = {"open", "high", "low", "close", "volume"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Missing candle columns: {sorted(missing)}")
    clean = df.copy()
    clean.index = pd.to_datetime(clean.index, utc=True)
    clean = clean[~clean.index.duplicated(keep="last")].sort_index()
    if not clean.index.is_monotonic_increasing:
        raise ValueError("Candle timestamps must increase")
    invalid = (
        (clean["high"] < clean[["open", "close", "low"]].max(axis=1))
        | (clean["low"] > clean[["open", "close", "high"]].min(axis=1))
    )
    if invalid.any():
        raise ValueError("Invalid OHLC candle")
    return clean


def filter_closed_candles(
    df: pd.DataFrame,
    timeframe: str,
    as_of: datetime | pd.Timestamp | None = None,
) -> pd.DataFrame:
    clean = normalize_candles(df)
    observed_at = _utc(as_of or datetime.now(timezone.utc))
    close_times = pd.DatetimeIndex(clean.index) + timeframe_to_timedelta(timeframe)
    mask = pd.Series(close_times <= observed_at, index=clean.index)
    return clean.loc[mask].copy()


def align_higher_timeframe_data(
    lower_index: pd.DatetimeIndex,
    higher: pd.DataFrame,
    higher_timeframe: str,
) -> pd.DataFrame:
    """Align only completed HTF bars to each lower-timeframe observation."""
    high = normalize_candles(higher).copy()
    high["_available_at"] = high.index + timeframe_to_timedelta(higher_timeframe)
    left = pd.DataFrame({"_observed_at": pd.to_datetime(lower_index, utc=True)})
    aligned = pd.merge_asof(
        left.sort_values("_observed_at"),
        high.reset_index(names="_opened_at").sort_values("_available_at"),
        left_on="_observed_at",
        right_on="_available_at",
        direction="backward",
    )
    return aligned.set_index("_observed_at")
