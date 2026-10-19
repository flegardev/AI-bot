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

    def validate(self, max_age: float, max_spread: float, now: datetime | None = None) -> None:
        if not all(math.isfinite(x) and x > 0 for x in (self.bid, self.ask)) or self.ask < self.bid:
            raise ValueError("invalid bid/ask quote")
        if self.timestamp.tzinfo is None:
            raise ValueError("quote timestamp must be timezone aware")
        age = ((now or datetime.now(timezone.utc)) - self.timestamp).total_seconds()
        if age < -5 or age > max_age:
            raise ValueError("stale or future quote")
        if self.spread > max_spread:
            raise ValueError("spread exceeds limit")
