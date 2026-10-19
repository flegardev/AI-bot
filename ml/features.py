"""One numeric feature schema for collection, fitting and paper inference."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.timeframes import filter_closed_candles, timeframe_to_timedelta

FEATURE_VERSION = "entry-v3"
FEATURE_COLS = ["ema_distance_pct", "rsi_14", "volume_ratio", "atr_pct", "sweep_direction", "signal_type",
                "volatility_20", "sweep_distance_atr", "regime_trend", "hour_sin", "hour_cos",
                "session_code", "spread_price", "spread_atr", "spread_observed"]


@dataclass(frozen=True)
class FeatureVector:
    timestamp: str
    values: tuple[float, ...]
    schema_version: str = FEATURE_VERSION

    def __post_init__(self):
        if self.schema_version != FEATURE_VERSION or len(self.values) != len(FEATURE_COLS):
            raise ValueError("feature schema mismatch")
        if not all(math.isfinite(value) for value in self.values):
            raise ValueError("features must be finite")

    def to_dict(self):
        return dict(zip(FEATURE_COLS, self.values, strict=True))

    @property
    def sha256(self):
        return hashlib.sha256(json.dumps([self.schema_version, self.timestamp, self.values]).encode()).hexdigest()


def build_features(bias, entry, sweep_direction, candles, *, as_of=None, sweep_level=None, spread=None):
    when = pd.Timestamp(as_of) if as_of is not None else candles.index[-1] + timeframe_to_timedelta("5m")
    frame = filter_closed_candles(candles, "5m", when)
    if len(frame) < 20:
        raise ValueError("feature vector requires 20 closed entry candles")
    close = frame["close"]
    price = float(close.iloc[-1])
    average = float(frame["volume"].tail(20).mean())
    ratio = float(frame["volume"].iloc[-1]) / average if average > 0 else 1.0
    ranges = pd.concat([frame["high"] - frame["low"], (frame["high"] - close.shift()).abs(),
                       (frame["low"] - close.shift()).abs()], axis=1).max(axis=1)
    atr = float(ranges.tail(14).mean())
    ema_distance = float(bias.get("ema_distance_pct", 0.0))
    observed = spread is not None or "spread" in frame
    if spread is None:
        spread = float(frame["spread"].iloc[-1]) if "spread" in frame else 0.0
    hour = when.hour + when.minute / 60
    volatility = float(close.pct_change().tail(20).std())
    values = {"ema_distance_pct": ema_distance, "rsi_14": float(entry.get("rsi_14", 50)),
        "volume_ratio": ratio, "atr_pct": float(entry.get("atr_pct", atr / price)),
        "sweep_direction": 1.0 if sweep_direction == "bullish" else -1.0,
        "signal_type": 1.0 if entry.get("type") == "CHoCH" else 0.0,
        "volatility_20": volatility if np.isfinite(volatility) else 0.0,
        "sweep_distance_atr": (price - float(sweep_level)) / atr if sweep_level is not None and atr > 0 else 0.0,
        "regime_trend": float(atr > 0 and abs(ema_distance) * price / atr > 1),
        "hour_sin": math.sin(2 * math.pi * hour / 24), "hour_cos": math.cos(2 * math.pi * hour / 24),
        "session_code": float(0 if hour < 7 else 1 if hour < 13 else 2 if hour < 21 else 3),
        "spread_price": float(spread), "spread_atr": float(spread) / atr if atr > 0 else 0.0,
        "spread_observed": float(observed)}
    return FeatureVector(when.isoformat(), tuple(values[key] for key in FEATURE_COLS))
