"""Predeclared research comparators, not claims of a profitable strategy."""
from __future__ import annotations

import hashlib
from collections import Counter

import pandas as pd

import config
from core.models import Direction, TradeSignal
from core.timeframes import filter_closed_candles, timeframe_to_timedelta


class BaselineStrategy:
    def __init__(self, kind="trend", period=20):
        if kind not in {"trend", "breakout"} or period < 5:
            raise ValueError("invalid baseline")
        self.kind, self.period = kind, period
        self.diagnostics: Counter[str] = Counter()
        self.last_reason = "not_evaluated"

    def evaluate(self, symbol, candles, as_of, *, can_long=True, can_short=True):
        self.diagnostics["evaluated"] += 1
        frame = filter_closed_candles(candles[config.TF_ENTRY], config.TF_ENTRY, as_of)
        if len(frame) < self.period + 2:
            self.diagnostics["insufficient_history"] += 1
            return None
        close = frame["close"]
        if self.kind == "trend":
            average = close.ewm(span=self.period, adjust=False).mean()
            long = close.iloc[-2] <= average.iloc[-2] and close.iloc[-1] > average.iloc[-1]
            short = close.iloc[-2] >= average.iloc[-2] and close.iloc[-1] < average.iloc[-1]
        else:
            history = frame.iloc[-self.period - 1:-1]
            long, short = close.iloc[-1] > history["high"].max(), close.iloc[-1] < history["low"].min()
        if not (long and can_long or short and can_short):
            self.diagnostics["no_entry"] += 1
            return None
        direction = Direction.LONG if long else Direction.SHORT
        ranges = pd.concat([frame["high"] - frame["low"],
            (frame["high"] - close.shift()).abs(), (frame["low"] - close.shift()).abs()], axis=1).max(axis=1)
        atr = float(ranges.tail(14).mean())
        price = float(close.iloc[-1])
        if atr <= 0:
            self.diagnostics["zero_atr"] += 1
            return None
        sign = 1 if long else -1
        timestamp = (frame.index[-1] + timeframe_to_timedelta(config.TF_ENTRY)).to_pydatetime()
        signal_id = hashlib.sha256(f"{symbol}|{self.kind}|{self.period}|{timestamp}".encode()).hexdigest()
        self.diagnostics["signals"] += 1
        return TradeSignal(symbol, direction, price, price - sign * 2 * atr,
            price + sign * 2 * atr * config.MIN_RISK_REWARD, timestamp,
            f"research {self.kind} {self.period}", signal_id=signal_id)
