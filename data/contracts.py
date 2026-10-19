"""Explicit source/time/price contract; unknown clock offsets are never guessed."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class MarketDataContract:
    source: str
    dataset_kind: str = "market"
    timestamp_basis: str = "utc"
    timezone: str = "UTC"
    price_basis: str = "midpoint"
    calendar: str = "fx_ny"
    spread_basis: str = "configured_constant"
    schema_version: str = "market-data-v1"

    def __post_init__(self):
        if not self.source or self.dataset_kind not in {"market", "synthetic", "unknown"}:
            raise ValueError("data contract requires source and dataset kind")
        if self.timestamp_basis not in {"utc", "broker_wall_time"}:
            raise ValueError("unsupported timestamp basis")
        if self.price_basis not in {"midpoint", "bid"}:
            raise ValueError("unsupported price basis")
        if self.calendar not in {"fx", "fx_ny", "24/7"}:
            raise ValueError("unsupported market calendar")
        if self.timestamp_basis == "utc" and self.timezone != "UTC":
            raise ValueError("UTC input must not also apply a broker timezone")
        # Validate the IANA name even when a dataset is empty.
        pd.Timestamp("2026-01-01").tz_localize(self.timezone)

    def timestamps(self, index) -> pd.DatetimeIndex:
        values = pd.DatetimeIndex(pd.to_datetime(index))
        if self.timestamp_basis == "broker_wall_time":
            if values.tz is not None:
                raise ValueError("wall-time input must be naive; do not double-convert timestamps")
            return values.tz_localize(self.timezone, ambiguous="raise", nonexistent="raise").tz_convert("UTC")
        if values.tz is None:
            raise ValueError("UTC contract requires explicitly timezone-aware timestamps")
        return values.tz_convert("UTC")

    def normalize(self, frame, constant_spread=0.0):
        result = frame.copy()
        result.index = self.timestamps(frame.index)
        if self.price_basis == "bid":
            spread = result["spread"] if "spread" in result else constant_spread
            result.loc[:, ["open", "high", "low", "close"]] = result[["open", "high", "low", "close"]].add(spread / 2, axis=0) if hasattr(spread, "index") else result[["open", "high", "low", "close"]] + spread / 2
        # The attached contract describes returned data, so a cache reload cannot convert twice.
        result.attrs["data_contract"] = {**asdict(self), "timestamp_basis": "utc", "timezone": "UTC", "price_basis": "midpoint"}
        result.attrs["input_contract"] = asdict(self)
        return result

    @property
    def sha256(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    @classmethod
    def read(cls, path):
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))
