"""Auditable bar replay: decide at close, execute at a later open, then process exits."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import tempfile
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import config
from brokers.base import BrokerCapabilities
from brokers.paper import PaperBrokerAdapter
from core.diagnostics import result_for
from core.models import Direction, OrderRequest, PositionSizeRequest, SymbolInfo, TradeSignal
from core.regimes import describe_market
from core.strategy_engine import StrategyEngine
from core.timeframes import _utc, filter_closed_candles, timeframe_to_timedelta
from data.quality import DataQualityError, inspect_dataset
from ml.predictor import FEATURE_COLS, MLPredictor
from persistence.store import TradingStore
from risk.engine import RiskEngine, RiskLimits


def _get_slice(df, as_of, timeframe="5m", min_bars=10):
    sliced = filter_closed_candles(df, timeframe, as_of)
    return sliced if len(sliced) >= min_bars else None


class BacktestEngine:
    def __init__(self, symbol=None, balance=None, artifact_dir=None, capabilities=None,
                 symbol_info=None, strategy=None, cost_multiplier=1.0):
        self.symbol = symbol or config.SYMBOL
        self.initial_balance = config.INITIAL_BALANCE if balance is None else balance
        self.balance = self.initial_balance
        self.artifact_dir = Path(artifact_dir or config.ARTIFACT_DIR)
        self.capabilities = capabilities or BrokerCapabilities()
        self.symbol_info = symbol_info or SymbolInfo(
            self.symbol, .00001, 1.0, 100_000, .01, 100, .01, leverage=100)
        self.strategy = strategy if strategy is not None else StrategyEngine()
        if cost_multiplier < 0 or not math.isfinite(cost_multiplier):
            raise ValueError("invalid cost multiplier")
        self.cost_multiplier = cost_multiplier
        self.trades: list[dict[str, Any]] = []
        self.rejections: Counter[str] = Counter()
        self.rejected_signals = 0
        self.equity_rows: list[dict[str, Any]] = []
        self.exposure_bars = 0
        self.decision_rows: list[dict[str, Any]] = []
        self.quality: dict[str, Any] = {}
        self.start = self.end = None

    def _download_data(self):
        raise DataQualityError("Explicit market data required; use the broker-specific CLI loader or --data-dir")

    def run(self, train_ml=False, data=None, *, start=None, end=None,
            strict_data=False, calendar="24/7"):
        if self.equity_rows or self.trades:
            raise ValueError("use a fresh BacktestEngine for each experiment")
        frames = data if data is not None else self._download_data()
        self.start = _utc(start) if start is not None else None
        self.end = _utc(end) if end is not None else None
        self.quality = inspect_dataset(frames, self.start, self.end, calendar,
                                       require_warmup=self.start is not None)
        if train_ml and getattr(getattr(self.strategy, "predictor", None), "enabled", False):
            raise ValueError("collect unfiltered rule trades before ML training")
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        (self.artifact_dir / "data_quality.json").write_text(json.dumps(self.quality, indent=2), encoding="utf-8")
        if strict_data and not self.quality["ok"]:
            raise DataQualityError("data quality failed; inspect data_quality.json before interpreting performance")
        if strict_data and (self.start is None or self.end is None):
            raise DataQualityError("strict research requires explicit start and end")
        frames = {tf: filter_closed_candles(frame, tf, self.end) for tf, frame in frames.items()}
        step = timeframe_to_timedelta(config.TF_ENTRY)
        entry_frame = frames[config.TF_ENTRY]
        if self.start is not None:
            entry_frame = entry_frame.loc[entry_frame.index >= self.start]
        if self.end is not None:
            entry_frame = entry_frame.loc[entry_frame.index + step <= self.end]
        if entry_frame.empty:
            empty_result = {"trades": [], "stats": {"message": "No data available.", "total_trades": 0}}
            self._write_artifacts(empty_result)
            return empty_result
        mult = self.cost_multiplier
        with tempfile.TemporaryDirectory() as directory:
            store = TradingStore(Path(directory) / "backtest.sqlite3")
            broker = PaperBrokerAdapter(store, self.initial_balance, self.symbol_info,
                spread=config.BACKTEST_SPREAD * mult,
                commission_per_lot=config.BACKTEST_FEE_RATE * self.symbol_info.contract_size * mult,
                slippage_pct=config.BACKTEST_SLIPPAGE_PCT * mult,
                swap_per_lot_day=config.BACKTEST_SWAP_PER_LOT_DAY * mult, persist_state=False)
            broker.set_clock(entry_frame.index[0].to_pydatetime())
            broker.connect()
            pending: list[tuple[int, TradeSignal]] = []
            risk = RiskEngine(RiskLimits(
                risk_per_trade=config.RISK_PER_TRADE,
                max_daily_loss_fraction=config.MAX_DAILY_LOSS_PCT,
                max_drawdown_fraction=config.MAX_DRAWDOWN_PCT,
                max_open_positions=config.MAX_OPEN_POSITIONS,
                max_portfolio_risk_fraction=config.SETTINGS.max_portfolio_risk_pct,
                max_symbol_exposure_fraction=config.SETTINGS.max_symbol_exposure_pct))
            high_water = day_equity = self.initial_balance
            current_day = None
            self.equity_rows.append({"timestamp": entry_frame.index[0].isoformat(),
                                    "balance": self.initial_balance, "equity": self.initial_balance})
            for index, (open_time, bar) in enumerate(entry_frame.iterrows()):
                opened = open_time.to_pydatetime()
                as_of = open_time + step
                if "spread" in bar:
                    broker.spread = float(bar["spread"]) * mult
                broker.set_clock(opened)
                if opened.date() != current_day:
                    current_day, day_equity = opened.date(), broker.get_account_state().equity
                # Pending decisions execute at this bar's open, never at the signal close.
                for due, original in list(pending):
                    if due > index:
                        continue
                    pending.remove((due, original))
                    reference = float(bar["open"])
                    adverse = broker._cost(reference) + broker.spread / 2
                    expected = reference + adverse if original.direction == Direction.LONG else reference - adverse
                    signal = replace(original, entry=expected)
                    valid = (signal.stop_loss < expected < signal.take_profit if signal.direction == Direction.LONG
                             else signal.take_profit < expected < signal.stop_loss)
                    if not valid:
                        self.rejected_signals += 1
                        self.rejections["gap crossed protection level"] += 1
                        self._execution_decision(original, opened, "gap_crossed_protection", False)
                        continue
                    account = broker.get_account_state()
                    sizing_request = PositionSizeRequest(signal.symbol, signal.direction, expected,
                        signal.stop_loss, config.RISK_PER_TRADE, account, self.symbol_info,
                        estimated_commission_per_lot=2 * broker.commission_per_lot
                        + broker._cost(signal.stop_loss) / self.symbol_info.tick_size * self.symbol_info.tick_value)
                    sizing = broker.calculate_position_size(sizing_request)
                    decision = risk.evaluate(signal, sizing_request, broker.get_open_positions(),
                        equity_high_water=high_water, start_of_day_equity=day_equity,
                        spread=broker.spread, price_timestamp=opened, now=opened, sizing=sizing)
                    if not decision.approved:
                        self.rejected_signals += 1
                        self.rejections.update(decision.reasons)
                        self._execution_decision(original, opened, "; ".join(decision.reasons), False)
                        continue
                    signal = replace(signal, metadata={**signal.metadata, "risk_amount": sizing.estimated_loss_at_stop})
                    order_result = broker.place_order(OrderRequest(signal, sizing.volume, f"bt-{signal.signal_id}",
                        reference_price=reference, execution_timestamp=opened))
                    if order_result.status.value not in {"filled", "partially_filled"}:
                        self.rejected_signals += 1
                        self.rejections[order_result.message or "broker rejection"] += 1
                    self._execution_decision(original, opened, order_result.status.value,
                                             order_result.status.value in {"filled", "partially_filled"})
                if broker.get_open_positions():
                    self.exposure_bars += 1
                for fill in broker.process_bar(self.symbol, bar, as_of.to_pydatetime()):
                    self._record_close(fill, store)
                # All indicator inputs must be available at the close of this bar.
                if not broker.get_open_positions() and not pending:
                    limits = {config.TF_BIAS: config.EMA_PERIOD * 5,
                              config.TF_LIQUIDITY: config.LIQUIDITY_LOOKBACK + 10,
                              config.TF_ENTRY: max(config.STRUCTURE_LOOKBACK, 20) + 10}
                    candles = {}
                    for tf, frame in frames.items():
                        cutoff = as_of - timeframe_to_timedelta(tf)
                        closed_index = frame.index.searchsorted(cutoff, side="right")
                        candles[tf] = frame.iloc[max(0, closed_index - limits[tf]):closed_index]
                    candidate_signal = self.strategy.evaluate(self.symbol, candles, as_of,
                        can_long=self.capabilities.long, can_short=self.capabilities.short)
                    evaluation = getattr(self.strategy, "last_evaluation", None)
                    self.decision_rows.append(evaluation.to_record() if evaluation else result_for(
                        self.symbol, candles, as_of, "signal" if candidate_signal else getattr(self.strategy, "last_reason", "no_entry"),
                        candidate_signal, getattr(self.strategy, "name", type(self.strategy).__name__)).to_record())
                    if candidate_signal:
                        candidate_signal.metadata.update(describe_market(candles[config.TF_ENTRY], as_of,
                            float(candles[config.TF_ENTRY]["spread"].iloc[-1]) if "spread" in candles[config.TF_ENTRY] else config.BACKTEST_SPREAD))
                        pending.append((index + max(1, config.BACKTEST_LATENCY_BARS), candidate_signal))
                else:
                    self.decision_rows.append({"timestamp": as_of.isoformat(), "symbol": self.symbol,
                        "accepted": False, "reason_code": "position_or_order_active", "phase": "portfolio"})
                account = broker.get_account_state()
                high_water = max(high_water, account.equity)
                self.equity_rows.append({"timestamp": as_of.isoformat(), "balance": account.balance,
                                        "equity": account.equity})
            last = entry_frame.iloc[-1]
            for position in list(broker.get_open_positions()):
                offset = -broker.spread / 2 if position.direction == Direction.LONG else broker.spread / 2
                fill = broker._close_at(position, float(last["close"]) + offset, "end_of_data",
                                       (entry_frame.index[-1] + step).to_pydatetime())
                self._record_close(fill, store)
            self.balance = broker.balance
            self.equity_rows[-1].update(balance=self.balance, equity=self.balance)
            broker.disconnect()
        result: dict[str, Any] = {"trades": self.trades, "stats": self._compute_stats(entry_frame),
                  "rejections": dict(self.rejections)}
        if train_ml:
            predictor = MLPredictor(enabled=False)
            begin = self.start or entry_frame.index[0]
            stop = self.end or entry_frame.index[-1] + step
            result["ml_training"] = predictor.train(self.trades,
                split_times=(begin + (stop - begin) * .6, begin + (stop - begin) * .8)) if self.trades else {"status": "no_trades"}
            (self.artifact_dir / "ml_training.json").write_text(json.dumps(result["ml_training"], indent=2, default=str), encoding="utf-8")
        self._write_artifacts(result)
        return result

    def _execution_decision(self, signal, timestamp, reason, accepted):
        self.decision_rows.append({"timestamp": timestamp.isoformat(), "symbol": self.symbol,
            "decision_id": signal.metadata.get("decision_id"), "signal_id": signal.signal_id,
            "phase": "execution", "reason_code": reason, "accepted": accepted})

    def _record_close(self, fill, store):
        event = next(event["payload"] for event in store.recent_events(10, "position_closed")
                     if event["entity_id"] == fill.position_id)
        features = event.get("features", {})
        pnl = float(event["pnl"])
        risk_amount = float(features.get("risk_amount", 0))
        self.trades.append({
            **{key: features[key] for key in FEATURE_COLS if key in features},
            **{key: features.get(key, "unknown") for key in ("utc_session", "volatility_bucket", "market_regime", "spread_bucket", "entry_month")},
            "position_id": fill.position_id, "signal_id": features.get("signal_id"),
            "decision_id": features.get("decision_id"),
            "feature_schema_version": features.get("feature_schema_version"),
            "feature_timestamp": features.get("feature_timestamp"),
            "timestamp": features.get("feature_timestamp", features.get("signal_timestamp", event["opened_at"])),
            "symbol": fill.symbol, "direction": fill.direction.value,
            "entry_timestamp": event["opened_at"], "exit_timestamp": fill.timestamp.isoformat(),
            "entry_price": event["entry_price"], "exit_price": fill.price,
            "stop_loss": event["stop_loss"], "take_profit": event["take_profit"],
            "volume": fill.volume, "fees": fill.commission, "swap": event.get("swap", 0),
            "slippage_price": float(event.get("entry_slippage", 0)) + fill.slippage,
            "pnl": pnl, "r_multiple": pnl / risk_amount if risk_amount else 0,
            "result": "win" if pnl > 0 else "loss", "exit_reason": event["reason"],
            "label_start": features.get("feature_timestamp", features.get("signal_timestamp", event["opened_at"])),
            "label_end": fill.timestamp.isoformat(), "matured": event["reason"] != "end_of_data",
            "dataset_kind": next(iter(self.quality["timeframes"].values())).get("data_contract", {}).get("dataset_kind", "unknown"),
        })

    def _compute_stats(self, frame):
        pnls = np.array([t["pnl"] for t in self.trades], dtype=float)
        positive, negative = pnls[pnls > 0], pnls[pnls < 0]
        equity = pd.DataFrame(self.equity_rows)
        values = equity["equity"]
        dd = values / values.cummax() - 1
        daily = equity.assign(timestamp=pd.to_datetime(equity["timestamp"], utc=True)).set_index("timestamp")["equity"].resample("1D").last().ffill()
        returns = daily.pct_change().dropna()
        downside = returns[returns < 0]
        max_dd = abs(float(dd.min()))
        durations, current = [], 0
        for value in dd:
            current = current + 1 if value < 0 else 0
            durations.append(current)
        days = max((frame.index[-1] + timeframe_to_timedelta(config.TF_ENTRY) - frame.index[0]).total_seconds() / 86400, 1)
        net = self.balance / self.initial_balance - 1
        annualized = (1 + net) ** (365 / days) - 1 if net > -1 else -1
        counts = dict(getattr(self.strategy, "diagnostics", {}))
        return {
            "initial_balance": self.initial_balance, "final_balance": self.balance,
            "net_return": net, "annualized_return": annualized, "maximum_drawdown": max_dd,
            "profit_factor": float(positive.sum() / abs(negative.sum())) if negative.size else None,
            "expectancy_per_trade": float(pnls.mean()) if pnls.size else 0.0,
            "win_rate": float((pnls > 0).mean()) if pnls.size else 0.0,
            "average_win": float(positive.mean()) if positive.size else 0,
            "average_loss": float(negative.mean()) if negative.size else 0,
            "sharpe_ratio": float(returns.mean() / returns.std() * math.sqrt(365)) if len(returns) > 1 and returns.std() > 0 else 0.0,
            "sortino_ratio": float(returns.mean() / downside.std() * math.sqrt(365)) if len(downside) > 1 and downside.std() > 0 else 0.0,
            "calmar_ratio": annualized / max_dd if max_dd else None,
            "exposure_time": self.exposure_bars / max(len(frame), 1),
            "total_fees": sum(t["fees"] for t in self.trades),
            "total_swap": sum(t["swap"] for t in self.trades),
            "total_trades": len(self.trades), "rejected_signals": self.rejected_signals,
            "rejection_reasons": dict(self.rejections), "strategy_diagnostics": counts,
            "long_short_results": {d: sum(t["pnl"] for t in self.trades if t["direction"] == d) for d in ("LONG", "SHORT")},
            "data_quality_passed": self.quality["ok"],
            "decision_records": len(self.decision_rows),
            "drawdown_recovery_bars": max(durations, default=0),
            "turnover_notional": sum(2 * t["entry_price"] * t["volume"] * self.symbol_info.contract_size for t in self.trades),
            "evidence_status": "historical_simulation_only" if self.quality["ok"] and len(pnls) else "insufficient_evidence",
            "assumptions": {"spread": config.BACKTEST_SPREAD * self.cost_multiplier,
                "commission_rate": config.BACKTEST_FEE_RATE * self.cost_multiplier,
                "slippage_fraction": config.BACKTEST_SLIPPAGE_PCT * self.cost_multiplier,
                "swap_per_lot_day": config.BACKTEST_SWAP_PER_LOT_DAY * self.cost_multiplier,
                "cost_multiplier": self.cost_multiplier, "latency_bars": max(1, config.BACKTEST_LATENCY_BARS),
                "simultaneous_sl_tp": "stop_first", "gap_policy": "fill_at_adverse_open",
                "entry": "delayed_bar_open_plus_spread_and_slippage", "ohlc_basis": "midpoint",
                "pnl_conversion": "constant symbol tick value; historical currency conversion not modeled",
                "swap_note": "constant daily debit; triple rollover not modeled",
                "rollover_mode": "excluded" if config.BACKTEST_SWAP_PER_LOT_DAY == 0 else "user_supplied_constant_not_verified",
                "sharpe_basis": "calendar daily returns, 365 periods/year"},
        }

    def _write_artifacts(self, result):
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        def write(name, value):
            (self.artifact_dir / name).write_text(json.dumps(value, indent=2, default=str, allow_nan=False), encoding="utf-8")
        write("backtest_summary.json", result["stats"])
        write("strategy_diagnostics.json", dict(getattr(self.strategy, "diagnostics", {})))
        decisions = pd.DataFrame(self.decision_rows)
        if decisions.empty:
            decisions = pd.DataFrame(columns=["decision_id", "timestamp", "symbol", "accepted", "reason_code"])
        for key in ("snapshots", "stages"):
            if key in decisions:
                decisions[key] = decisions[key].map(lambda value: json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else "")
        decisions.to_csv(self.artifact_dir / "signal_decisions.csv", index=False)
        write("signal_decisions.json", self.decision_rows)
        pd.DataFrame(self.trades, columns=list(dict.fromkeys(FEATURE_COLS + [
            "position_id", "signal_id", "decision_id", "timestamp", "symbol", "direction", "entry_timestamp", "exit_timestamp",
            "entry_price", "exit_price", "stop_loss", "take_profit", "volume", "fees", "swap", "slippage_price", "pnl", "r_multiple", "result", "exit_reason",
            "label_start", "label_end", "matured", "feature_schema_version", "feature_timestamp", "dataset_kind", "utc_session", "volatility_bucket", "market_regime", "spread_bucket", "entry_month"]))).to_csv(self.artifact_dir / "trades.csv", index=False)
        groups = []
        trades = pd.DataFrame(self.trades)
        if not trades.empty:
            for dimension in ("symbol", "utc_session", "volatility_bucket", "market_regime", "spread_bucket", "entry_month"):
                for value, group in trades.groupby(dimension):
                    groups.append({"dimension": dimension, "value": value, "trades": len(group),
                        "net_pnl": float(group["pnl"].sum()), "expectancy": float(group["pnl"].mean()),
                        "win_rate": float((group["pnl"] > 0).mean())})
        pd.DataFrame(groups, columns=["dimension", "value", "trades", "net_pnl", "expectancy", "win_rate"]).to_csv(self.artifact_dir / "regime_report.csv", index=False)
        equity = pd.DataFrame(self.equity_rows, columns=["timestamp", "balance", "equity"])
        equity.to_csv(self.artifact_dir / "equity_curve.csv", index=False)
        if not equity.empty:
            values = equity["equity"]
            pd.DataFrame({"timestamp": equity["timestamp"], "drawdown": values / values.cummax() - 1}).to_csv(self.artifact_dir / "drawdown_curve.csv", index=False)
            dated = equity.assign(timestamp=pd.to_datetime(equity["timestamp"], utc=True)).set_index("timestamp")["equity"]
            ends = dated.resample("ME").last()
            previous = ends.shift(1).fillna(self.initial_balance)
            (ends / previous - 1).rename("return").to_csv(self.artifact_dir / "monthly_returns.csv")
        else:
            pd.DataFrame(columns=["timestamp", "drawdown"]).to_csv(self.artifact_dir / "drawdown_curve.csv", index=False)
            pd.DataFrame(columns=["timestamp", "return"]).to_csv(self.artifact_dir / "monthly_returns.csv", index=False)
        write("backtest_config_snapshot.json", {"symbol": self.symbol, "start": self.start, "end": self.end,
            "initial_balance": self.initial_balance, "risk_per_trade": config.RISK_PER_TRADE,
            "ema_period": config.EMA_PERIOD, "liquidity_lookback": config.LIQUIDITY_LOOKBACK,
            "structure_lookback": config.STRUCTURE_LOOKBACK, "min_risk_reward": config.MIN_RISK_REWARD,
            "ml_enabled": bool(getattr(getattr(self.strategy, "predictor", None), "enabled", False)),
            "data_digests": {tf: report.get("sha256") for tf, report in self.quality["timeframes"].items()},
            **result["stats"].get("assumptions", {})})
        try:
            revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1],
                                      capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            revision = "unavailable"
        summary = ["# Diagnostic backtest", "", f"Data quality: {'PASSED' if self.quality['ok'] else 'FAILED / DIAGNOSTIC ONLY'}",
            f"Closed trades: {len(self.trades)}", f"Decision records: {len(self.decision_rows)}",
            f"Net P&L: {self.balance - self.initial_balance:.6f}",
            "", "Strategy funnel:", json.dumps(dict(getattr(self.strategy, "diagnostics", {})), indent=2),
            "", "Historical simulation only. No profitability or forward-readiness conclusion."]
        (self.artifact_dir / "summary.md").write_text("\n".join(summary), encoding="utf-8")
        write("artifacts_manifest.json", {"git_sha": revision, "seed": 42,
            "data_contracts": {tf: report.get("data_contract") for tf, report in self.quality["timeframes"].items()},
            "files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.artifact_dir.iterdir()
                      if path.is_file() and path.name != "artifacts_manifest.json"}})
