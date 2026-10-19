"""Strict research coverage checks. Never invent or forward-fill missing prices."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

import config
from core.timeframes import _utc, normalize_candles, timeframe_to_timedelta


class DataQualityError(ValueError):
    pass


def warmup_start(start: datetime) -> datetime:
    # Calendar buffer includes weekends; exact bar sufficiency is checked separately.
    return start - max(
        timeframe_to_timedelta(config.TF_BIAS) * (config.EMA_PERIOD + 20) * 2,
        timedelta(days=3),
    )


def market_hours(index: pd.DatetimeIndex, calendar: str) -> np.ndarray:
    if calendar == "24/7":
        return np.ones(len(index), dtype=bool)
    if calendar == "fx_ny":
        local = index.tz_convert("America/New_York")
        day, hour = local.dayofweek, local.hour
        return np.asarray((day < 4) | ((day == 4) & (hour < 17)) | ((day == 6) & (hour >= 17)))
    if calendar != "fx":
        raise ValueError("calendar must be fx, fx_ny or 24/7")
    # Explicit approximate FX calendar. DST, holidays and broker breaks require review.
    day, hour = index.dayofweek, index.hour
    return np.asarray((day < 4) | ((day == 4) & (hour < 22)) | ((day == 6) & (hour >= 22)))


def inspect_frame(frame: pd.DataFrame, timeframe: str, start=None, end=None,
                  calendar: str = "24/7", min_warmup: int = 0) -> dict[str, Any]:
    issues = []
    step = pd.Timedelta(timeframe_to_timedelta(timeframe))
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        issues.append("timestamps must have an explicit timezone")
    if frame.index.has_duplicates:
        issues.append("duplicate timestamps")
    if not frame.index.is_monotonic_increasing:
        issues.append("timestamps not increasing")
    try:
        clean = normalize_candles(frame)
        values = clean[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            issues.append("nonfinite OHLCV")
        if (values[:, :4] <= 0).any() or (values[:, 4] < 0).any():
            issues.append("nonpositive price or negative volume")
        if "spread" in clean and (not np.isfinite(clean["spread"].to_numpy(dtype=float)).all() or (clean["spread"] < 0).any()):
            issues.append("invalid observed spread")
    except (ValueError, TypeError) as exc:
        return {"ok": False, "issues": issues + [str(exc)], "rows": len(frame)}
    if clean.empty:
        return {"ok": False, "issues": issues + ["empty history"], "rows": 0}
    nanos = np.asarray(pd.DatetimeIndex(clean.index).as_unit("ns"), dtype="datetime64[ns]").view(np.int64)
    if (nanos % step.value != 0).any():
        issues.append("timestamps not aligned to timeframe")
    begin = _utc(start) if start is not None else clean.index[0]
    stop = _utc(end) if end is not None else clean.index[-1] + step
    expected = pd.date_range(begin.ceil(step), stop - step, freq=step)
    expected = expected[market_hours(expected, calendar)]
    available = clean.index[(clean.index >= begin) & (clean.index + step <= stop)]
    missing = expected.difference(pd.DatetimeIndex(available))
    coverage = 1 - len(missing) / len(expected) if len(expected) else 0.0
    if len(missing):
        issues.append(f"{len(missing)} missing expected bars")
    if not len(expected):
        issues.append("empty evaluation window")
    warmup = int((clean.index + step <= begin).sum())
    if warmup < min_warmup:
        issues.append(f"warm-up {warmup} < {min_warmup}")
    warmup_missing = pd.DatetimeIndex([])
    if min_warmup and warmup:
        warmup_expected = pd.date_range(clean.index[0], begin - step, freq=step)
        warmup_expected = warmup_expected[market_hours(warmup_expected, calendar)]
        warmup_missing = warmup_expected.difference(pd.DatetimeIndex(clean.index))
        if len(warmup_missing):
            issues.append(f"{len(warmup_missing)} missing warm-up bars")
    return {
        "ok": not issues, "issues": issues, "rows": len(clean),
        "first": clean.index[0].isoformat(), "last": clean.index[-1].isoformat(),
        "evaluation_rows": len(available), "expected_rows": len(expected),
        "coverage": coverage, "warmup_rows": warmup, "missing_bars": len(missing),
        "warmup_missing_bars": len(warmup_missing),
        "missing_examples": [str(item) for item in missing[:10]], "calendar": calendar,
        "calendar_note": "fx_ny uses New York Sunday/Friday 17:00 with DST; fx is legacy fixed UTC; broker holidays/breaks need an explicit calendar",
        "data_contract": frame.attrs.get("data_contract", {"dataset_kind": "unknown", "source": "unspecified"}),
        "sha256": hashlib.sha256(pd.util.hash_pandas_object(clean, index=True).to_numpy().tobytes()).hexdigest(),
    }


def inspect_dataset(frames, start=None, end=None, calendar="24/7", require_warmup=False):
    minimums = {config.TF_BIAS: config.EMA_PERIOD,
                config.TF_LIQUIDITY: config.LIQUIDITY_LOOKBACK,
                config.TF_ENTRY: max(config.STRUCTURE_LOOKBACK, 20)}
    reports = {tf: inspect_frame(frames.get(tf, pd.DataFrame()), tf, start, end, calendar,
                               minimum if require_warmup else 0)
               for tf, minimum in minimums.items()}
    return {"ok": all(report["ok"] for report in reports.values()), "timeframes": reports}
