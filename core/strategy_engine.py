from __future__ import annotations

import hashlib
from datetime import datetime

import pandas as pd

import config
from core.models import Direction, TradeSignal
from core.timeframes import filter_closed_candles, timeframe_to_timedelta
from risk.manager import calculate_stop_loss
from strategy.bias import get_bias_details
from strategy.entry import get_entry_details
from strategy.liquidity import detect_liquidity_sweep


class StrategyEngine:
    """Single broker-independent strategy path used by every mode."""

    def evaluate(
        self,
        symbol: str,
        candles: dict[str, pd.DataFrame],
        as_of: datetime | pd.Timestamp,
        *,
        can_long: bool = True,
        can_short: bool = True,
    ) -> TradeSignal | None:
        closed = {
            timeframe: filter_closed_candles(frame, timeframe, as_of)
            for timeframe, frame in candles.items()
        }
        bias = get_bias_details(closed[config.TF_BIAS])
        if bias.get("bias") is None:
            return None
        sweep = detect_liquidity_sweep(closed[config.TF_LIQUIDITY])
        if not sweep["swept"]:
            return None
        direction = Direction.LONG if sweep["direction"] == "bullish" else Direction.SHORT
        if direction.value != bias["bias"]:
            return None
        if (direction == Direction.LONG and not can_long) or (direction == Direction.SHORT and not can_short):
            return None
        entry = get_entry_details(closed[config.TF_ENTRY], sweep["direction"])
        if not entry["signal"]:
            return None
        price = float(entry["current_price"])
        stop = calculate_stop_loss(direction.value, float(sweep["level"]), float(entry["atr_14"]))
        distance = abs(price - stop)
        take_profit = price + distance * config.MIN_RISK_REWARD if direction == Direction.LONG else price - distance * config.MIN_RISK_REWARD
        signal_timestamp = (
            closed[config.TF_ENTRY].index[-1]
            + timeframe_to_timedelta(config.TF_ENTRY)
        ).to_pydatetime()
        signal_id = hashlib.sha256(
            f"{symbol}|{direction.value}|{signal_timestamp.isoformat()}".encode()
        ).hexdigest()
        return TradeSignal(
            symbol, direction, price, stop, take_profit, signal_timestamp,
            f"{bias['bias']} bias; {sweep['direction']} sweep; {entry['type']}",
            signal_id=signal_id,
            metadata={
                "ema_distance_pct": float(bias.get("ema_distance_pct", 0)),
                "rsi_14": float(entry.get("rsi_14", 50)),
                "atr_pct": float(entry.get("atr_pct", 0)),
                "sweep_direction": sweep["direction"],
                "signal_type": entry["type"],
            },
        )
