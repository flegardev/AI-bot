"""Mandatory diagnostic-simulator acceptance cases; synthetic values are explicit."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd
import pytest

import main
from brokers.paper import PaperBrokerAdapter
from core.models import Direction, OrderRequest, SymbolInfo, TradeSignal
from core.strategy_engine import StrategyEngine
from data.contracts import MarketDataContract
from data.quality import inspect_frame
from persistence.store import TradingStore


def setup(tmp_path, spread=0, fee=0, slip=0):
    store = TradingStore(tmp_path / "ledger.db")
    broker = PaperBrokerAdapter(store, 1000, SymbolInfo("TEST", 1, 1, 1, 1, 100, 1, leverage=10),
                                spread=spread, commission_per_lot=fee, slippage=slip)
    when = datetime(2024, 1, 15, tzinfo=timezone.utc)
    broker.set_clock(when)
    signal = TradeSignal("TEST", Direction.LONG, 100, 95, 110, when, "synthetic acceptance fixture")
    return store, broker, signal


def bar(when, opened=100, high=101, low=99, close=100):
    return pd.Series({"open": opened, "high": high, "low": low, "close": close, "volume": 1.}, name=pd.Timestamp(when))


@pytest.mark.parametrize("side", [Direction.LONG, Direction.SHORT])
def test_long_short_round_trip_charges_spread_and_two_fees(tmp_path, side):
    store, broker, signal = setup(tmp_path, spread=2, fee=2, slip=.1)
    if side == Direction.SHORT:
        signal = replace(signal, direction=side, stop_loss=105, take_profit=90)
    result = broker.place_order(OrderRequest(signal, 1, "costs"))
    broker.process_bar("TEST", bar(signal.timestamp), signal.timestamp + timedelta(minutes=5))
    broker.close_position(result.position_id)
    payload = store.recent_events(1, "position_closed")[0]["payload"]
    assert payload["commission"] == 4
    assert payload["pnl"] == pytest.approx(-6.2)
    assert broker.balance == pytest.approx(993.8)


def test_crash_during_exit_rolls_back_event_position_balance_and_cursor(tmp_path):
    store, broker, signal = setup(tmp_path)
    broker.place_order(OrderRequest(signal, 1, "open"))
    stopped = bar(signal.timestamp, low=94)
    original = store.set_state
    def fail_cursor(key, value):
        if key.startswith("paper_last_bar"):
            raise RuntimeError("injected crash after ledger writes")
        original(key, value)
    with patch.object(store, "set_state", side_effect=fail_cursor), pytest.raises(RuntimeError):
        broker.apply_completed_bar("TEST", stopped, "5m", signal.timestamp + timedelta(minutes=5))
    assert broker.balance == 1000
    assert len(broker.get_open_positions()) == 1
    assert not store.recent_events(10, "position_closed")
    restored = PaperBrokerAdapter(store, 1000, broker.get_symbol_info("TEST"))
    restored.apply_completed_bar("TEST", stopped, "5m", signal.timestamp + timedelta(minutes=5))
    again = PaperBrokerAdapter(store, 1000, broker.get_symbol_info("TEST"))
    again.apply_completed_bar("TEST", stopped, "5m", signal.timestamp + timedelta(minutes=5))
    assert again.balance == 995
    assert len(store.recent_events(10, "position_closed")) == 1


def test_incomplete_candle_cannot_update_paper_state(tmp_path):
    store, broker, signal = setup(tmp_path)
    broker.place_order(OrderRequest(signal, 1, "open"))
    with pytest.raises(ValueError, match="incomplete"):
        broker.apply_completed_bar("TEST", bar(signal.timestamp, low=90), "5m", signal.timestamp)
    assert len(broker.get_open_positions()) == 1
    assert store.get_state("paper_last_bar_TEST") is None


def test_gap_stop_uses_adverse_open_and_open_timestamp(tmp_path):
    store, broker, signal = setup(tmp_path)
    broker.place_order(OrderRequest(signal, 1, "open"))
    when = signal.timestamp + timedelta(minutes=5)
    fills = broker.apply_completed_bar("TEST", bar(when, opened=90, high=92, low=89, close=91), "5m", when + timedelta(minutes=5))
    assert fills[0].price == 90
    assert fills[0].timestamp == when


def test_snapshot_and_rejection_are_unchanged_by_future_prices():
    when = pd.Timestamp("2024-01-15", tz="UTC")
    frames = {tf: pd.DataFrame([bar(at) for at in pd.date_range(when, periods=240, freq=freq)])
              for tf, freq in [("1h", "1h"), ("15m", "15min"), ("5m", "5min")]}
    cutoff = when + pd.Timedelta(hours=2)
    first = StrategyEngine().evaluate_result("TEST", frames, cutoff)
    changed = {tf: frame.copy() for tf, frame in frames.items()}
    for frame in changed.values():
        frame.loc[frame.index >= cutoff, ["open", "high", "low", "close"]] = [1000, 1001, 999, 1000]
    second = StrategyEngine().evaluate_result("TEST", changed, cutoff)
    assert first.reason_code.startswith("insufficient_history")
    assert first.snapshots == second.snapshots
    assert first.decision_id == second.decision_id
    assert first.stages[0]["status"] == "rejected"


@pytest.mark.parametrize("day,open_utc", [("2024-01-07", "22:00"), ("2024-07-07", "21:00")])
def test_fx_calendar_observes_new_york_dst(day, open_utc):
    start = pd.Timestamp(f"{day} {open_utc}", tz="UTC")
    index = pd.date_range(start, periods=4, freq="1h")
    frame = pd.DataFrame([bar(at) for at in index])
    assert inspect_frame(frame, "1h", start, start + pd.Timedelta(hours=4), "fx_ny")["ok"]


def test_explicit_broker_timezone_normalizes_without_guessing():
    contract = MarketDataContract("fixture", "synthetic", "broker_wall_time", "Europe/Helsinki")
    index = contract.timestamps(["2024-07-01 12:00:00", "2024-12-01 12:00:00"])
    assert list(index.hour) == [9, 10]
    with pytest.raises(ValueError):
        contract.timestamps(pd.date_range("2024-01-01", periods=1, tz="UTC"))


def test_replay_helper_filters_incomplete_candles(tmp_path):
    store, broker, signal = setup(tmp_path)
    now = datetime.now(timezone.utc)
    signal = replace(signal, timestamp=now)
    broker.place_order(OrderRequest(signal, 1, "open", execution_timestamp=now))
    incomplete = bar(pd.Timestamp(now).floor("5min"), low=90)
    main.replay_paper_exits(broker, pd.DataFrame([incomplete]), store, "TEST")
    assert len(broker.get_open_positions()) == 1
