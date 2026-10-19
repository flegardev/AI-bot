"""Synthetic event fixtures test the frozen paper path; no brokerage execution."""
import json
from dataclasses import asdict, replace
from unittest.mock import patch

import pandas as pd
import pytest

import config
from brokers.paper import PaperBrokerAdapter
from core.models import Direction, OrderRequest, TradeSignal
from core.quotes import Quote
from data.contracts import MarketDataContract
from data.fixtures import diagnostic_fixture
from forward.cli import diagnostic
from forward.frozen import freeze_candidate, verify_freeze
from forward.runner import ForwardPaperRunner, single_writer


def fixture(tmp_path):
    frames, info, start, end = diagnostic_fixture(tmp_path / "fixture")
    selection = {"selected": {"name": "no_trade", "period": 20}, "selection_end": str(start - pd.Timedelta(minutes=5))}
    root = tmp_path / "frozen"
    freeze_candidate(selection, MarketDataContract(**frames["5m"].attrs["data_contract"]), root, symbol_info=info, start=start)
    runner = ForwardPaperRunner(root / "freeze.json")
    return frames, info, start, end, runner


def quote(when, price=100):
    return Quote(price - .00005, price + .00005, when.to_pydatetime())


def test_frozen_flat_forward_and_duplicate_candle_are_restart_safe(tmp_path):
    frames, _, start, _, runner = fixture(tmp_path)
    first = runner.observe(frames, quote(start), received_at=start)
    assert first["reason_code"] == "no_trade_benchmark"
    again = ForwardPaperRunner(runner.path)
    assert again.observe(frames, quote(start), received_at=start)["status"] == "duplicate_candle"
    report = again.export()
    assert report["event_counts"]["forward_decision"] == 1
    assert report["dataset_kind"] == "synthetic" and report["broker_orders_sent"] is False


def test_freeze_rejects_modified_configuration_and_manifest(tmp_path):
    _, _, _, _, runner = fixture(tmp_path)
    with patch.object(config, "BACKTEST_SPREAD", 123), pytest.raises(ValueError, match="configuration changed"):
        verify_freeze(runner.path)
    changed = json.loads(runner.path.read_text())
    changed["mode"] = "live"
    runner.path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="modified"):
        verify_freeze(runner.path)


def test_invalid_quote_contract_or_time_prevents_new_decisions(tmp_path):
    frames, _, start, _, runner = fixture(tmp_path)
    with pytest.raises(ValueError, match="stale or future"):
        runner.observe(frames, quote(start + pd.Timedelta(hours=3)), received_at=start)
    changed = {tf: frame.copy() for tf, frame in frames.items()}
    changed["5m"].attrs["data_contract"] = {**changed["5m"].attrs["data_contract"], "source": "different"}
    with pytest.raises(ValueError, match="contract changed"):
        runner.observe(changed, quote(start), received_at=start)
    runner.observe(frames, quote(start), received_at=start)
    with pytest.raises(ValueError, match="predates|out-of-order"):
        runner.observe(frames, quote(start - pd.Timedelta(minutes=1)), received_at=start - pd.Timedelta(minutes=1))
    assert runner.export()["event_counts"]["forward_failure"] == 3


def test_existing_position_advances_to_stop_once_after_restart(tmp_path):
    frames, info, start, _, runner = fixture(tmp_path)
    runner.observe(frames, quote(start), received_at=start)
    paper = PaperBrokerAdapter(runner.store, 10_000, info)
    signal = TradeSignal(info.symbol, Direction.LONG, 100, 99.6, 102, start.to_pydatetime(), "injected synthetic open position")
    paper.place_order(OrderRequest(signal, 1, "fixture-entry", reference_price=100, execution_timestamp=start.to_pydatetime()))
    for _ in range(2):
        restored = ForwardPaperRunner(runner.path)
        restored.observe(frames, quote(start + pd.Timedelta(minutes=5)), received_at=start + pd.Timedelta(minutes=5))
    events = runner.store.recent_events(10, "position_closed")
    assert len(events) == 1 and events[0]["payload"]["reason"] == "stop_loss"


def test_single_writer_lock_and_no_winner_guard(tmp_path):
    frames, info, start, _, runner = fixture(tmp_path)
    with single_writer(runner.root / "writer.lock"), pytest.raises(OSError):
        runner.observe(frames, quote(start), received_at=start)
    with pytest.raises(ValueError, match="no candidate"):
        freeze_candidate({"selected": None}, MarketDataContract("fixture", "synthetic"), tmp_path / "invalid", symbol_info=info)
    selection = {"selected": {"name": "no_trade", "period": 20}, "selection_end": str(start)}
    with pytest.raises(ValueError, match="cannot be backdated"):
        freeze_candidate(selection, MarketDataContract("market"), tmp_path / "invalid", symbol_info=info, start=start + pd.Timedelta(days=1))


def test_actual_paper_fill_uses_quote_and_risk_gate_never_market_broker(tmp_path):
    frames, info, start, _, runner = fixture(tmp_path)
    signal = TradeSignal(info.symbol, Direction.LONG, 100, 99, 102, start.to_pydatetime(), "synthetic signal", signal_id="repeatable")
    # Inject only a strategy signal, preserving real risk/sizing/paper execution code.
    runner.strategy.evaluate = lambda *a, **k: signal
    runner.strategy.last_reason = "signal"
    runner.observe(frames, quote(start), received_at=start)
    fills = runner.store.recent_events(10, "forward_execution")
    # 1bp configured slippage exceeds the real instrument price-unit movement bound here.
    assert not fills
    assert "signal-to-fill" in runner.store.recent_events(1, "forward_risk")[0]["payload"]["reasons"][0]
    # A narrower, correctly priced fixture exercises the fill through every guard.
    frames2 = {tf: frame.copy() / 100 for tf, frame in frames.items()}
    for frame in frames2.values():
        frame.attrs["data_contract"] = asdict(MarketDataContract(**frames["5m"].attrs["data_contract"]))
    when = start + pd.Timedelta(minutes=5)
    runner.strategy.evaluate = lambda *a, **k: replace(signal, entry=1, stop_loss=.99, take_profit=1.02, timestamp=when.to_pydatetime())
    runner.observe(frames2, quote(when, 1), received_at=when)
    fills = runner.store.recent_events(10, "forward_execution")
    assert len(fills) == 1 and fills[0]["payload"]["fill_price"] == pytest.approx(1.00015)
    assert runner.export()["broker_orders_sent"] is False


def test_forward_diagnostic_has_explicit_synthetic_provenance(tmp_path):
    report = diagnostic(tmp_path / "diagnostic")
    assert report["observations"] == 12 and report["closed_trades"] == 0
    assert report["dataset_kind"] == "synthetic" and report["status"] == "unproven"
