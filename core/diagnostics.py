"""Versioned decision records containing only information available at decision time."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import pandas as pd

from core.models import TradeSignal
from core.timeframes import filter_closed_candles, timeframe_to_timedelta

STAGES = ("data", "bias", "liquidity", "alignment", "capability", "entry", "protection", "ml")


@dataclass(frozen=True)
class EvaluationResult:
    decision_id: str
    timestamp: str
    symbol: str
    strategy: str
    accepted: bool
    reason_code: str
    snapshots: dict[str, dict[str, Any]]
    stages: tuple[dict[str, str], ...]
    signal: TradeSignal | None = None
    schema_version: str = "decision-v1"

    def to_record(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "decision_id": self.decision_id,
                "timestamp": self.timestamp, "symbol": self.symbol, "strategy": self.strategy,
                "accepted": self.accepted, "reason_code": self.reason_code,
                "signal_id": self.signal.signal_id if self.signal else None,
                "snapshots": self.snapshots, "stages": list(self.stages)}


def snapshot_ids(candles, as_of):
    snapshots = {}
    for timeframe, frame in candles.items():
        try:
            closed = filter_closed_candles(frame, timeframe, as_of)
            digest = hashlib.sha256(pd.util.hash_pandas_object(closed, index=True).to_numpy().tobytes()).hexdigest()
            snapshots[timeframe] = {"sha256": digest, "rows": len(closed),
                "last_closed_at": (closed.index[-1] + timeframe_to_timedelta(timeframe)).isoformat() if not closed.empty else None}
        except (ValueError, TypeError, KeyError):
            snapshots[timeframe] = {"rows": len(frame), "invalid": True}
    return snapshots


def result_for(symbol, candles, as_of, reason, signal=None, strategy="smc_baseline_v1"):
    snapshots = snapshot_ids(candles, as_of)
    timestamp = pd.Timestamp(as_of).isoformat()
    digest = hashlib.sha256(json.dumps({"symbol": symbol, "at": timestamp, "strategy": strategy,
                                       "snapshots": snapshots}, sort_keys=True).encode()).hexdigest()
    failed: str | None
    if reason.startswith("insufficient_history") or reason in {"missing_timeframe", "invalid_data"}:
        failed = "data"
    else:
        failed = {"no_bias": "bias", "no_liquidity_sweep": "liquidity", "bias_sweep_mismatch": "alignment",
            "direction_unavailable": "capability", "no_entry": "entry", "no_entry_confirmation": "entry", "no_trade_benchmark": "entry", "invalid_stop": "protection",
            "ml_model_unavailable": "ml", "ml_temporal_overlap": "ml", "ml_rejected": "ml", "ml_synthetic_model": "ml"}.get(reason)
    stages = []
    stopped = False
    for stage in STAGES:
        status = "not_reached" if stopped else "rejected" if stage == failed else "passed"
        stages.append({"stage": stage, "status": status, "reason_code": reason if stage == failed else ""})
        stopped |= stage == failed
    return EvaluationResult(digest, timestamp, symbol, strategy, signal is not None, reason,
                            snapshots, tuple(stages), signal)
