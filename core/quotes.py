"""Shared execution-time quote validation; bounds use absolute price units."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    timestamp: datetime

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    def validate_prices(self, max_spread: float) -> None:
        if not all(math.isfinite(x) and x > 0 for x in (self.bid, self.ask)) or self.ask < self.bid:
            raise ValueError("invalid bid/ask quote")
        if self.spread > max_spread:
            raise ValueError("spread exceeds limit")

    def age_seconds(self, now: datetime | None = None) -> float:
        """Signed age: a future source timestamp must remain negative."""
        if self.timestamp.tzinfo is None:
            raise ValueError("quote timestamp must be timezone aware")
        observed = now or datetime.now(timezone.utc)
        if observed.tzinfo is None:
            raise ValueError("observation timestamp must be timezone aware")
        age = (observed - self.timestamp).total_seconds()
        if not math.isfinite(age):
            raise ValueError("invalid quote timestamp")
        return age

    def validate_timestamp(self, max_age: float, now: datetime | None = None) -> None:
        age = self.age_seconds(now)
        if age < -5 or age > max_age:
            raise ValueError("stale or future quote")

    def validate(self, max_age: float, max_spread: float, now: datetime | None = None) -> None:
        self.validate_prices(max_spread)
        self.validate_timestamp(max_age, now)
