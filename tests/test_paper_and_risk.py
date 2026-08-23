from datetime import datetime, timezone

import pandas as pd

from brokers.paper import PaperBrokerAdapter
from core.models import AccountState, Direction, OrderRequest, PositionSizeRequest, SymbolInfo, TradeSignal
from persistence.store import TradingStore
from risk.engine import RiskEngine, RiskLimits


def setup(tmp_path, policy="stop_first"):
    info = SymbolInfo("TEST", 1, 1, 1, 1, 100, 1, leverage=10)
    store = TradingStore(tmp_path / "trading.db")
    broker = PaperBrokerAdapter(store, 1000, info, simultaneous_policy=policy)
    broker.connect()
    signal = TradeSignal("TEST", Direction.LONG, 100, 95, 110, datetime.now(timezone.utc), "test")
    return store, broker, signal


def open_one(broker, signal):
    return broker.place_order(OrderRequest(signal, 1, "one"))


def test_simultaneous_sl_tp_defaults_to_stop(tmp_path):
    store, broker, signal = setup(tmp_path)
    open_one(broker, signal)
    fills = broker.process_bar("TEST", pd.Series({"open": 100, "high": 111, "low": 94, "close": 105}))
    assert fills[0].price == 95
    assert broker.balance == 995


def test_gap_through_stop_fills_at_open(tmp_path):
    store, broker, signal = setup(tmp_path)
    open_one(broker, signal)
    fills = broker.process_bar("TEST", pd.Series({"open": 90, "high": 92, "low": 88, "close": 91}))
    assert fills[0].price == 90
    assert broker.balance == 990


def test_tp_and_short_behavior(tmp_path):
    store, broker, long_signal = setup(tmp_path)
    short_signal = TradeSignal("TEST", Direction.SHORT, 100, 105, 90, datetime.now(timezone.utc), "short")
    broker.place_order(OrderRequest(short_signal, 1, "short"))
    fills = broker.process_bar("TEST", pd.Series({"open": 100, "high": 102, "low": 89, "close": 91}))
    assert fills[0].price == 90
    assert broker.balance == 1010


def test_idempotent_order_retry_does_not_duplicate(tmp_path):
    store, broker, signal = setup(tmp_path)
    first = broker.place_order(OrderRequest(signal, 1, "stable-id"))
    second = broker.place_order(OrderRequest(signal, 1, "stable-id"))
    assert first.position_id == second.position_id
    assert len(broker.get_open_positions()) == 1


def test_restart_reconstructs_positions(tmp_path):
    store, broker, signal = setup(tmp_path)
    open_one(broker, signal)
    restored = PaperBrokerAdapter(store, 1000, broker.get_symbol_info("TEST"))
    assert len(restored.get_open_positions()) == 1


def test_risk_uses_equity_for_daily_loss(tmp_path):
    _, broker, signal = setup(tmp_path)
    info = broker.get_symbol_info("TEST")
    account = AccountState(1000, 900, 900, 0, -100, 0)
    sizing_request = PositionSizeRequest("TEST", Direction.LONG, 100, 95, 0.01, account, info)
    decision = RiskEngine(RiskLimits(max_daily_loss_fraction=0.05)).evaluate(
        signal, sizing_request, [], equity_high_water=1000, start_of_day_equity=1000,
        price_timestamp=datetime.now(timezone.utc)
    )
    assert not decision.approved
    assert "daily loss limit reached" in decision.reasons
    assert "maximum drawdown reached" in decision.reasons


def test_max_open_and_portfolio_risk(tmp_path):
    _, broker, signal = setup(tmp_path)
    open_one(broker, signal)
    account = broker.get_account_state()
    request = PositionSizeRequest("TEST", Direction.LONG, 100, 95, 0.01, account, broker.get_symbol_info("TEST"))
    decision = RiskEngine(RiskLimits(max_open_positions=1, max_portfolio_risk_fraction=0.01)).evaluate(
        signal, request, broker.get_open_positions(), equity_high_water=1000,
        start_of_day_equity=1000, portfolio_risk=10,
        price_timestamp=datetime.now(timezone.utc)
    )
    assert "maximum open positions reached" in decision.reasons
    assert "maximum portfolio risk reached" in decision.reasons
