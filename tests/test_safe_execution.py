from datetime import datetime, timezone

import pytest

from brokers.base import BrokerCapabilities, CriticalExecutionError
from core.models import (
    Direction,
    OrderResult,
    OrderStatus,
    Position,
    RiskDecision,
    SizingResult,
    TradeSignal,
)
from execution.service import SafeExecutionService
from persistence.store import TradingStore


class FakeBroker:
    capabilities = BrokerCapabilities()

    def __init__(self, protected=True, attach=True, status=OrderStatus.FILLED):
        self.protected, self.attach, self.status = protected, attach, status
        self.closed = False
        self.positions = []
        self.place_calls = 0

    def place_order(self, request):
        self.place_calls += 1
        if self.status == OrderStatus.REJECTED:
            return OrderResult(self.status, message="rejected")
        self.positions = [Position(
            "p1", request.signal.symbol, request.signal.direction, request.volume,
            request.signal.entry, request.signal.stop_loss if self.protected else 0,
            request.signal.take_profit if self.protected else 0,
            datetime.now(timezone.utc), "o1"
        )]
        return OrderResult(self.status, "o1", "p1", request.volume, request.signal.entry)

    def get_open_positions(self, symbol=None):
        return self.positions

    def modify_position(self, position_id, stop_loss, take_profit):
        if self.attach:
            self.positions[0].stop_loss, self.positions[0].take_profit = stop_loss, take_profit
        return self.attach

    def close_position(self, position_id):
        self.closed = True
        self.positions = []
        return OrderResult(OrderStatus.FILLED, position_id=position_id)

    def get_open_orders(self, symbol=None):
        return []

    def cancel_order(self, order_id):
        return True


def decision():
    return RiskDecision(True, (), SizingResult(100, 50, 1, 10), "s")


def signal():
    return TradeSignal("TEST", Direction.LONG, 100, 95, 110, datetime.now(timezone.utc), "test")


def test_rejected_order_is_returned(tmp_path):
    service = SafeExecutionService(FakeBroker(status=OrderStatus.REJECTED), TradingStore(tmp_path / "db"))
    assert service.execute(signal(), decision()).status == OrderStatus.REJECTED


def test_partial_fill_is_verified(tmp_path):
    service = SafeExecutionService(FakeBroker(status=OrderStatus.PARTIALLY_FILLED), TradingStore(tmp_path / "db"))
    assert service.execute(signal(), decision()).status == OrderStatus.PARTIALLY_FILLED


def test_unprotected_fill_attaches_protection(tmp_path):
    broker = FakeBroker(protected=False, attach=True)
    service = SafeExecutionService(broker, TradingStore(tmp_path / "db"))
    service.execute(signal(), decision())
    assert broker.positions[0].stop_loss == 95
    assert broker.positions[0].take_profit == 110


def test_unprotected_fill_emergency_closes_and_disables(tmp_path):
    broker = FakeBroker(protected=False, attach=False)
    service = SafeExecutionService(broker, TradingStore(tmp_path / "db"))
    with pytest.raises(CriticalExecutionError):
        service.execute(signal(), decision())
    assert broker.closed
    assert not service.entries_enabled
    assert service.store.recent_events(1, "critical_unprotected_position")


def test_duplicate_execution_returns_persisted_result_without_second_order(tmp_path):
    broker = FakeBroker()
    service = SafeExecutionService(broker, TradingStore(tmp_path / "db"))
    same_signal = signal()
    first = service.execute(same_signal, decision())
    second = service.execute(same_signal, decision())
    assert first.order_id == second.order_id
    assert broker.place_calls == 1
