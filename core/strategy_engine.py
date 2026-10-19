from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime

import numpy as np
import pandas as pd

import config
from core.diagnostics import result_for
from core.models import Direction, TradeSignal
from core.timeframes import filter_closed_candles, timeframe_to_timedelta
from risk.manager import calculate_stop_loss
from strategy.bias import get_bias_details
from strategy.entry import get_entry_details
from strategy.liquidity import detect_liquidity_sweep


class StrategyEngine:
    """Single broker-independent strategy path used by every mode."""

    def __init__(self, predictor=None):
        from ml.predictor import MLPredictor
        self.predictor = predictor if predictor is not None else MLPredictor()
        self.diagnostics: Counter[str] = Counter()
        self.last_reason = "not_evaluated"

    def reject(self, reason):
        self.last_reason = reason
        self.diagnostics[reason] += 1
        return None

    def evaluate(self, symbol, candles, as_of, *, can_long=True, can_short=True):
        return self.evaluate_result(symbol, candles, as_of, can_long=can_long, can_short=can_short).signal

    def evaluate_result(self, symbol, candles, as_of, *, can_long=True, can_short=True):
        try:
            signal = self._evaluate_signal(symbol, candles, as_of, can_long=can_long, can_short=can_short)
        except (ValueError, TypeError, KeyError):
            signal = self.reject("invalid_data")
        self.last_evaluation = result_for(symbol, candles, as_of, self.last_reason, signal)
        if signal is not None:
            signal.metadata["decision_id"] = self.last_evaluation.decision_id
        return self.last_evaluation

    def _evaluate_signal(
        self,
        symbol: str,
        candles: dict[str, pd.DataFrame],
        as_of: datetime | pd.Timestamp,
        *,
        can_long: bool = True,
        can_short: bool = True,
    ) -> TradeSignal | None:
        self.diagnostics["evaluated"] += 1
        minimums = {config.TF_BIAS: config.EMA_PERIOD,
                    config.TF_LIQUIDITY: config.LIQUIDITY_LOOKBACK,
                    config.TF_ENTRY: max(config.STRUCTURE_LOOKBACK, 20)}
        if any(tf not in candles for tf in minimums):
            return self.reject("missing_timeframe")
        closed = {
            timeframe: filter_closed_candles(frame, timeframe, as_of)
            for timeframe, frame in candles.items()
        }
        for tf, minimum in minimums.items():
            values = closed[tf][["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
            if not np.isfinite(values).all() or (values[:, :4] <= 0).any() or (values[:, 4] < 0).any():
                return self.reject("invalid_data")
            if len(closed[tf]) < minimum:
                return self.reject(f"insufficient_history_{tf}")
        bias = get_bias_details(closed[config.TF_BIAS])
        if bias.get("bias") is None:
            return self.reject("no_bias")
        sweep = detect_liquidity_sweep(closed[config.TF_LIQUIDITY])
        if not sweep["swept"]:
            return self.reject("no_liquidity_sweep")
        direction = Direction.LONG if sweep["direction"] == "bullish" else Direction.SHORT
        if direction.value != bias["bias"]:
            return self.reject("bias_sweep_mismatch")
        if (direction == Direction.LONG and not can_long) or (direction == Direction.SHORT and not can_short):
            return self.reject("direction_unavailable")
        entry = get_entry_details(closed[config.TF_ENTRY], sweep["direction"])
        if not entry["signal"]:
            return self.reject("no_entry_confirmation")
        price = float(entry["current_price"])
        stop = calculate_stop_loss(direction.value, float(sweep["level"]), float(entry["atr_14"]))
        distance = abs(price - stop)
        if distance <= 0 or (direction == Direction.LONG and stop >= price) or (direction == Direction.SHORT and stop <= price):
            return self.reject("invalid_stop")
        take_profit = price + distance * config.MIN_RISK_REWARD if direction == Direction.LONG else price - distance * config.MIN_RISK_REWARD
        signal_timestamp = (
            closed[config.TF_ENTRY].index[-1]
            + timeframe_to_timedelta(config.TF_ENTRY)
        ).to_pydatetime()
        signal_id = hashlib.sha256(
            f"{symbol}|{direction.value}|{signal_timestamp.isoformat()}".encode()
        ).hexdigest()
        from ml.predictor import MLPredictor
        features = MLPredictor.extract_features(bias, entry, sweep["direction"], closed[config.TF_ENTRY])
        self.diagnostics["rule_signals"] += 1
        if self.predictor.enabled:
            if not self.predictor.is_trained:
                return self.reject("ml_model_unavailable")
            # A model may never score a signal inside its training/selection window.
            trained_through = self.predictor.metadata.get("selection_end")
            if not trained_through or pd.Timestamp(trained_through) >= pd.Timestamp(signal_timestamp):
                return self.reject("ml_temporal_overlap")
            accepted, probability = self.predictor.is_confident(features)
            if not accepted:
                return self.reject("ml_rejected")
            features["ml_probability"] = probability
        self.last_reason = "signal"
        self.diagnostics["signals"] += 1
        return TradeSignal(
            symbol, direction, price, stop, take_profit, signal_timestamp,
            f"{bias['bias']} bias; {sweep['direction']} sweep; {entry['type']}",
            signal_id=signal_id,
            metadata=features,
        )
