"""Named, transparent comparators. Original SMC thresholds remain unchanged."""
from __future__ import annotations

import hashlib
from collections import Counter

import pandas as pd

import config
from core.models import Direction, TradeSignal
from core.strategy_engine import StrategyEngine
from core.timeframes import filter_closed_candles, timeframe_to_timedelta
from ml.predictor import MLPredictor
from strategy.baselines import BaselineStrategy


class NoTrade:
    name = "no_trade"

    def __init__(self):
        self.diagnostics: Counter[str] = Counter()
        self.last_reason = "no_trade_benchmark"

    def evaluate(self, *args, **kwargs):
        self.diagnostics.update(evaluated=1, no_trade_benchmark=1)
        return None


class ATRBreakout:
    name = "atr_breakout_v1"

    def __init__(self, period=20, atr_buffer=.25):
        self.period, self.atr_buffer = period, atr_buffer
        self.diagnostics: Counter[str] = Counter()
        self.last_reason = "not_evaluated"

    def evaluate(self, symbol, candles, as_of, *, can_long=True, can_short=True):
        self.diagnostics["evaluated"] += 1
        frame = filter_closed_candles(candles[config.TF_ENTRY], config.TF_ENTRY, as_of)
        if len(frame) < self.period + 2:
            self.last_reason = "insufficient_history"
            self.diagnostics[self.last_reason] += 1
            return None
        history = frame.iloc[-self.period - 1:-1]
        ranges = pd.concat([history["high"] - history["low"],
            (history["high"] - history["close"].shift()).abs(),
            (history["low"] - history["close"].shift()).abs()], axis=1).max(axis=1)
        atr = float(ranges.tail(14).mean())
        price = float(frame["close"].iloc[-1])
        long = price > float(history["high"].max()) + self.atr_buffer * atr
        short = price < float(history["low"].min()) - self.atr_buffer * atr
        if atr <= 0 or not (long and can_long or short and can_short):
            self.last_reason = "no_entry"
            self.diagnostics[self.last_reason] += 1
            return None
        direction, sign = (Direction.LONG, 1) if long else (Direction.SHORT, -1)
        when = (frame.index[-1] + timeframe_to_timedelta(config.TF_ENTRY)).to_pydatetime()
        identity = hashlib.sha256(f"{symbol}|{self.name}|{self.period}|{self.atr_buffer}|{when}".encode()).hexdigest()
        self.last_reason = "signal"
        self.diagnostics["signals"] += 1
        return TradeSignal(symbol, direction, price, price - sign * 2 * atr,
            price + sign * 2 * atr * config.MIN_RISK_REWARD, when, self.name, signal_id=identity,
            metadata={"atr_pct": atr / price, "strategy": self.name})


def original_bias(frame):
    """Preserve original main's equal-EMA SHORT tie behavior in the comparator."""
    if len(frame) < config.EMA_PERIOD:
        return {"bias": None}
    ema = float(frame["close"].ewm(span=config.EMA_PERIOD, adjust=False).mean().iloc[-1])
    price = float(frame["close"].iloc[-1])
    return {"bias": "LONG" if price > ema else "SHORT", "ema_distance_pct": (price - ema) / ema}


def candidate(name, period=20):
    if name in {"smc", "smc_baseline_v1"}:
        result = StrategyEngine(MLPredictor(enabled=False), bias_provider=original_bias)
        result.name = "smc_baseline_v1"
        return result
    if name == "no_trade":
        return NoTrade()
    if name == "trend_momentum_v1":
        trend = BaselineStrategy("trend", period)
        trend.name = "trend_momentum_v1"
        return trend
    if name == "atr_breakout_v1":
        return ATRBreakout(period)
    raise ValueError(f"unknown declared strategy: {name}")
