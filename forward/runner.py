"""Frozen observation loop. Only the injected PaperBrokerAdapter can execute orders."""
from __future__ import annotations

import json
import os
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path

import numpy as np
import pandas as pd

import config
from brokers.paper import PaperBrokerAdapter
from core.diagnostics import result_for
from core.models import Direction, PositionSizeRequest, SymbolInfo
from core.timeframes import filter_closed_candles, timeframe_to_timedelta
from data.quality import inspect_frame
from execution.service import SafeExecutionService
from forward.frozen import verify_freeze
from ml.features import FEATURE_COLS
from ml.predictor import MLPredictor
from persistence.store import TradingStore
from risk.engine import RiskEngine, RiskLimits
from strategy.competition import candidate


@contextmanager
def single_writer(path):
    """OS lock is released on a crash; a stale lock file does not strand evaluation."""
    with Path(path).open("a+b") as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            msvcrt = import_module("msvcrt")
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl = import_module("fcntl")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class ForwardPaperRunner:
    def __init__(self, frozen_path):
        self.path = Path(frozen_path)
        self.root = self.path.parent
        self.manifest = verify_freeze(self.path)
        self.store = TradingStore(self.root / "forward.sqlite3")
        self.info = SymbolInfo(**self.manifest["symbol_info"])
        self.strategy = candidate(**self.manifest["selected"])
        if self.manifest["model_sha256"]:
            self.strategy.predictor = MLPredictor(str(self.root / "model.pkl"), enabled=True)

    def observe(self, frames, quote, *, received_at=None):
        received = pd.Timestamp(received_at or datetime.now(timezone.utc))
        try:
            if received.tzinfo is None:
                raise ValueError("receipt timestamp must be timezone aware")
            with single_writer(self.root / "writer.lock"):
                verify_freeze(self.path)
                return self._observe(frames, quote, received)
        except (ValueError, RuntimeError, OSError, KeyError) as exc:
            self.store.record_event("forward_failure", {"error": str(exc), "received_at": str(received)}, mode="paper")
            self.export()
            raise

    def _observe(self, frames, quote, received):
        contract = self.manifest["data_contract"]
        start = pd.Timestamp(self.manifest["forward_start"])
        if contract["dataset_kind"] != "synthetic" and abs((received - pd.Timestamp.now(tz="UTC")).total_seconds()) > 5:
            raise ValueError("market forward observations must be received in real time")
        quote.validate(config.SETTINGS.stale_price_seconds, config.SETTINGS.max_spread, received.to_pydatetime())
        as_of = min(received, pd.Timestamp(quote.timestamp))
        if as_of < start:
            raise ValueError("observation predates frozen forward period")
        closed = {}
        minimums = {"1h": config.EMA_PERIOD, "15m": config.LIQUIDITY_LOOKBACK,
                    "5m": max(20, config.STRUCTURE_LOOKBACK)}
        for tf, minimum in minimums.items():
            frame = frames[tf]
            if frame.attrs.get("data_contract") != contract:
                raise ValueError(f"{tf} data contract changed after freeze")
            closed[tf] = filter_closed_candles(frame, tf, as_of)
            quality = inspect_frame(closed[tf], tf, calendar=contract["calendar"])
            if not quality["ok"] or len(closed[tf]) < minimum:
                raise ValueError(f"{tf} invalid coverage/warm-up: {quality['issues']}")
            age = as_of - (closed[tf].index[-1] + timeframe_to_timedelta(tf))
            if age > 2 * timeframe_to_timedelta(tf):
                raise ValueError(f"{tf} stale completed candles")
        last_received = self.store.get_state("last_received")
        if last_received and received < pd.Timestamp(last_received):
            raise ValueError("out-of-order forward observation")
        broker = PaperBrokerAdapter(self.store, config.INITIAL_BALANCE, self.info, spread=quote.spread,
            commission_per_lot=config.BACKTEST_FEE_RATE * self.info.contract_size,
            slippage_pct=config.BACKTEST_SLIPPAGE_PCT, swap_per_lot_day=config.BACKTEST_SWAP_PER_LOT_DAY)
        cursor_key = f"paper_last_bar_{self.info.symbol}"
        cursor = self.store.get_state(cursor_key)
        entry_frame = closed["5m"]
        if broker.get_open_positions():
            if cursor and pd.Timestamp(cursor) < entry_frame.index[0] - timeframe_to_timedelta("5m"):
                raise ValueError("forward position needs older candles; backfill from the persisted cursor")
            for at, bar in entry_frame.iterrows():
                if cursor is None or pd.Timestamp(str(at)) > pd.Timestamp(cursor):
                    # Historical quote spreads are unavailable unless explicitly supplied per bar.
                    broker.spread = float(bar.get("spread", config.BACKTEST_SPREAD))
                    broker.apply_completed_bar(self.info.symbol, bar, "5m", as_of.to_pydatetime())
        else:
            self.store.set_state(cursor_key, entry_frame.index[-1].isoformat())
        broker.spread = quote.spread
        midpoint = (quote.bid + quote.ask) / 2
        broker.process_bar(self.info.symbol, pd.Series({"open": midpoint, "high": midpoint,
            "low": midpoint, "close": midpoint}, name=received), received.to_pydatetime())
        account = broker.get_account_state()
        self.store.set_state("equity_high_water", max(float(self.store.get_state("equity_high_water", account.equity)), account.equity))
        day_key = f"start_equity_{received.date()}"
        if self.store.get_state(day_key) is None:
            self.store.set_state(day_key, account.equity)
        self.store.record_event("forward_quote", {"received_at": str(received), "source_time": str(quote.timestamp),
            "latency_seconds": (received - pd.Timestamp(quote.timestamp)).total_seconds(), "spread": quote.spread,
            "bid": quote.bid, "ask": quote.ask}, mode="paper", symbol=self.info.symbol)
        key = entry_frame.index[-1].isoformat()
        outcome = {"status": "duplicate_candle", "accepted": False}
        if self.store.get_state("last_decision_candle") != key:
            if hasattr(self.strategy, "evaluate_result"):
                evaluation = self.strategy.evaluate_result(self.info.symbol, closed, as_of,
                    can_long=self.info.can_long, can_short=self.info.can_short, spread=quote.spread)
            else:
                signal = self.strategy.evaluate(self.info.symbol, closed, as_of,
                    can_long=self.info.can_long, can_short=self.info.can_short)
                evaluation = result_for(self.info.symbol, closed, as_of, self.strategy.last_reason, signal, self.strategy.name)
            outcome = evaluation.to_record()
            self.store.record_event("forward_decision", outcome, mode="paper", symbol=self.info.symbol)
            signal = evaluation.signal
            if signal:
                direction = 1 if signal.direction == Direction.LONG else -1
                fill = midpoint + direction * (quote.spread / 2 + broker._cost(midpoint))
                move = abs(fill - signal.entry)
                if move > config.SETTINGS.max_slippage:
                    self.store.record_event("forward_risk", {"approved": False, "reasons": ["signal-to-fill move exceeds limit"],
                        "price_move": move}, mode="paper")
                else:
                    self._execute(broker, replace(signal, entry=fill), quote, received, midpoint, signal.entry)
                    self._record_drift(signal.metadata)
            # An interrupted fill is protected by signal idempotency; no re-evaluation of completed cycles.
            self.store.set_state("last_decision_candle", key)
        self.store.set_state("last_received", str(received))
        self.store.record_event("forward_account", asdict(broker.get_account_state()), mode="paper")
        self.export()
        return outcome

    def _execute(self, broker, signal, quote, received, midpoint, signal_price):
        account = broker.get_account_state()
        high = max(float(self.store.get_state("equity_high_water", account.equity)), account.equity)
        self.store.set_state("equity_high_water", high)
        day_key = f"start_equity_{received.date()}"
        day = float(self.store.get_state(day_key, account.equity))
        self.store.set_state(day_key, day)
        request = PositionSizeRequest(signal.symbol, signal.direction, signal.entry, signal.stop_loss,
            config.RISK_PER_TRADE, account, self.info, 2 * broker.commission_per_lot
            + config.SETTINGS.max_slippage / self.info.tick_size * self.info.tick_value)
        positions = broker.get_open_positions()
        portfolio = sum(abs(p.entry_price - p.stop_loss) / self.info.tick_size * self.info.tick_value * p.volume for p in positions)
        notional = sum(p.entry_price * self.info.contract_size * p.volume for p in positions)
        service = SafeExecutionService(broker, self.store)
        if self.store.get_state("pending_entry"):
            service.kill_switch()
            raise RuntimeError("interrupted entry requires reconciliation; frozen run cannot reset its kill switch")
        limits = RiskLimits(config.RISK_PER_TRADE, config.MAX_DAILY_LOSS_PCT, config.MAX_DRAWDOWN_PCT,
            config.MAX_OPEN_POSITIONS, config.SETTINGS.max_portfolio_risk_pct,
            config.SETTINGS.max_symbol_exposure_pct, config.SETTINGS.max_spread,
            config.SETTINGS.max_slippage, config.SETTINGS.stale_price_seconds)
        decision = RiskEngine(limits).evaluate(signal, request, positions, equity_high_water=high,
            start_of_day_equity=day, spread=quote.spread, price_timestamp=quote.timestamp,
            portfolio_risk=portfolio, symbol_notional=notional, live_entries_enabled=service.entries_enabled,
            now=received.to_pydatetime())
        self.store.record_event("forward_risk", asdict(decision), mode="paper")
        result = service.execute(signal, decision, reference_price=midpoint, execution_timestamp=received.to_pydatetime())
        if result:
            self.store.record_event("forward_execution", {**asdict(result), "assumed_fee_per_lot": broker.commission_per_lot,
                "quote_to_fill_move": result.fill_price - midpoint, "signal_to_fill_move": result.fill_price - signal_price}, mode="paper")

    def _record_drift(self, features):
        reference = (self.manifest.get("model_card") or {}).get("feature_reference")
        if not reference:
            return
        z = {name: abs((features[name] - reference["mean"][name]) / reference["std"][name])
             for name in FEATURE_COLS if reference["std"][name] > 1e-12}
        self.store.record_event("feature_drift", {"absolute_z": z, "over_3_sigma": [name for name, value in z.items() if value > 3],
            "action": "report_only_no_retraining", "sample": "accepted rule features"}, mode="paper")

    def export(self):
        with self.store.connection() as connection:
            rows = connection.execute("SELECT * FROM events ORDER BY id").fetchall()
        events = [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]
        counts = Counter(row["event_type"] for row in events)
        def payloads(kind):
            return [row["payload"] for row in events if row["event_type"] == kind]
        quotes, closes, accounts = payloads("forward_quote"), payloads("position_closed"), payloads("forward_account")
        equity = pd.Series([row["equity"] for row in accounts], dtype=float)
        summary = {"freeze_id": self.manifest["freeze_id"], "dataset_kind": self.manifest["data_contract"]["dataset_kind"],
            "status": "unproven", "mode": "frozen_paper", "broker_orders_sent": False, "event_counts": dict(counts),
            "observations": len(quotes), "closed_trades": len(closes), "net_realized_pnl": sum(row["pnl"] for row in closes),
            "latest_equity": float(equity.iloc[-1]) if len(equity) else None,
            "maximum_observed_drawdown": abs(float((equity / equity.cummax() - 1).min())) if len(equity) else None,
            "mean_quote_latency_seconds": float(np.mean([row["latency_seconds"] for row in quotes])) if quotes else None,
            "mean_spread": float(np.mean([row["spread"] for row in quotes])) if quotes else None,
            "failure_reasons": dict(Counter(row["error"] for row in payloads("forward_failure"))),
            "limitations": ["paper fills only; no broker execution or profitability validation", "partial entry candle OHLC extrema are excluded; sampled quotes still advance protection",
                "historical spreads use configured fallback when per-bar spread is unavailable", "broker-specific commissions/rollover and market gaps require validation",
                "feature drift is reported only on rule features that passed the filter", "a longer untouched prospective sample is required"]}
        (self.root / "forward_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        with (self.root / "events.jsonl").open("w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")
        for kind in ("forward_quote", "position_closed", "forward_account", "forward_decision", "forward_risk", "feature_drift"):
            pd.DataFrame(payloads(kind)).to_csv(self.root / f"{kind}.csv", index=False)
        return summary
