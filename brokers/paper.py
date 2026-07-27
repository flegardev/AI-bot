from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pandas as pd

from brokers.base import BrokerCapabilities
from core.models import (
    AccountState,
    Direction,
    Fill,
    OrderRequest,
    OrderResult,
    OrderStatus,
    Position,
    PositionSizeRequest,
    SymbolInfo,
)
from persistence.store import TradingStore
from risk.sizing import calculate_mt5_position_size


class PaperBrokerAdapter:
    capabilities = BrokerCapabilities(long=True, short=True, native_protection=True)

    def __init__(
        self,
        store: TradingStore,
        initial_balance: float = 10_000.0,
        symbol_info: SymbolInfo | None = None,
        spread: float = 0.0,
        commission_per_lot: float = 0.0,
        slippage: float = 0.0,
        simultaneous_policy: str = "stop_first",
    ):
        self.store = store
        self.connected = False
        self.spread = spread
        self.slippage = slippage
        self.commission_per_lot = commission_per_lot
        self.simultaneous_policy = simultaneous_policy
        self._symbol_info = symbol_info or SymbolInfo(
            "EURUSD", 0.00001, 1.0, 100_000, 0.01, 100.0, 0.01, leverage=100
        )
        self.balance = float(store.get_state("paper_balance", initial_balance))
        self.realized_today = float(store.get_state("paper_realized_today", 0.0))
        self.positions: dict[str, Position] = {}
        for raw in store.get_state("paper_positions", []):
            raw["direction"] = Direction(raw["direction"])
            raw["opened_at"] = datetime.fromisoformat(raw["opened_at"])
            self.positions[raw["position_id"]] = Position(**raw)

    def connect(self) -> None:
        self.connected = True
        self.store.record_event("startup", {}, mode="paper")

    def disconnect(self) -> None:
        self._persist()
        self.connected = False
        self.store.record_event("shutdown", {}, mode="paper")

    def get_candles(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        raise NotImplementedError("Paper adapter requires an injected market data source")

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        if symbol == self._symbol_info.symbol:
            return self._symbol_info
        return SymbolInfo(symbol, **{k: v for k, v in self._symbol_info.__dict__.items() if k != "symbol"})

    def get_account_state(self) -> AccountState:
        floating = sum(position.unrealized_pnl for position in self.positions.values())
        used = sum(
            position.entry_price * self.get_symbol_info(position.symbol).contract_size * position.volume
            / max(self.get_symbol_info(position.symbol).leverage, 1)
            for position in self.positions.values()
        )
        return AccountState(
            self.balance, self.balance + floating, max(0.0, self.balance - used), used,
            floating, self.realized_today
        )

    def calculate_position_size(self, request: PositionSizeRequest):
        return calculate_mt5_position_size(request)

    def get_open_positions(self, symbol: str | None = None) -> list[Position]:
        return [p for p in self.positions.values() if symbol is None or p.symbol == symbol]

    def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return []

    def place_order(self, request: OrderRequest) -> OrderResult:
        previous = self.store.get_idempotent_response(request.client_order_id)
        if previous:
            previous["status"] = OrderStatus(previous["status"])
            return OrderResult(**previous)
        info = self.get_symbol_info(request.signal.symbol)
        margin = request.signal.entry * info.contract_size * request.volume / max(info.leverage, 1)
        if margin > self.get_account_state().free_margin:
            result = OrderResult(OrderStatus.REJECTED, message="insufficient margin")
        elif request.volume < info.volume_min or request.volume > info.volume_max:
            result = OrderResult(OrderStatus.REJECTED, message="invalid volume")
        else:
            adverse = self.slippage + self.spread / 2
            fill_price = request.signal.entry + adverse if request.signal.direction == Direction.LONG else request.signal.entry - adverse
            order_id, position_id = uuid4().hex, uuid4().hex
            position = Position(
                position_id, request.signal.symbol, request.signal.direction, request.volume,
                fill_price, request.signal.stop_loss, request.signal.take_profit,
                datetime.now(timezone.utc), order_id
            )
            self.positions[position_id] = position
            result = OrderResult(
                OrderStatus.FILLED, order_id, position_id, request.volume, fill_price, "paper fill"
            )
            self.store.record_event("fill", result.__dict__, mode="paper", symbol=position.symbol, entity_id=position_id)
            self._persist()
        saved = {**result.__dict__, "status": result.status.value}
        self.store.save_idempotent_response(request.client_order_id, saved)
        return result

    def process_bar(self, symbol: str, bar: pd.Series, timestamp: datetime | None = None) -> list[Fill]:
        """Apply OHLC exits; gaps fill at the adverse open and ambiguous bars stop first."""
        fills: list[Fill] = []
        if not self.positions:
            return fills
        for position in list(self.get_open_positions(symbol)):
            is_long = position.direction == Direction.LONG
            stop_gap = bar["open"] <= position.stop_loss if is_long else bar["open"] >= position.stop_loss
            tp_gap = bar["open"] >= position.take_profit if is_long else bar["open"] <= position.take_profit
            stop_touch = bar["low"] <= position.stop_loss if is_long else bar["high"] >= position.stop_loss
            tp_touch = bar["high"] >= position.take_profit if is_long else bar["low"] <= position.take_profit
            reason = None
            price = 0.0
            if stop_gap:
                reason, price = "stop_gap", float(bar["open"])
            elif tp_gap:
                reason, price = "take_profit_gap", float(bar["open"])
            elif stop_touch and tp_touch:
                if self.simultaneous_policy == "take_profit_first":
                    reason, price = "take_profit", position.take_profit
                else:
                    reason, price = "stop_loss", position.stop_loss
            elif stop_touch:
                reason, price = "stop_loss", position.stop_loss
            elif tp_touch:
                reason, price = "take_profit", position.take_profit
            if reason:
                fills.append(self._close_at(position, price, reason, timestamp))
            else:
                mark = float(bar["close"])
                multiplier = 1 if is_long else -1
                position.unrealized_pnl = (
                    (mark - position.entry_price) * multiplier * position.volume * self.get_symbol_info(symbol).contract_size
                )
        self._persist()
        return fills

    def _close_at(self, position: Position, price: float, reason: str, timestamp: datetime | None) -> Fill:
        direction_mult = 1 if position.direction == Direction.LONG else -1
        info = self.get_symbol_info(position.symbol)
        adverse = self.slippage
        fill_price = price - adverse if position.direction == Direction.LONG else price + adverse
        gross = (fill_price - position.entry_price) * direction_mult * position.volume * info.contract_size
        commission = self.commission_per_lot * position.volume * 2
        pnl = gross - commission
        self.balance += pnl
        self.realized_today += pnl
        del self.positions[position.position_id]
        fill = Fill(
            uuid4().hex, position.position_id, position.symbol, position.direction, fill_price,
            position.volume, timestamp or datetime.now(timezone.utc), commission, adverse
        )
        self.store.record_event(
            "position_closed", {
                **fill.__dict__,
                "reason": reason,
                "pnl": pnl,
                "entry_price": position.entry_price,
                "stop_loss": position.stop_loss,
                "take_profit": position.take_profit,
                "volume": position.volume,
                "opened_at": position.opened_at.isoformat(),
            },
            mode="paper", symbol=position.symbol, entity_id=position.position_id
        )
        return fill

    def modify_position(self, position_id: str, stop_loss: float, take_profit: float) -> bool:
        position = self.positions.get(position_id)
        if not position:
            return False
        position.stop_loss, position.take_profit = stop_loss, take_profit
        self._persist()
        return True

    def close_position(self, position_id: str) -> OrderResult:
        position = self.positions.get(position_id)
        if not position:
            return OrderResult(OrderStatus.REJECTED, message="position not found")
        self._close_at(position, position.entry_price, "manual", None)
        return OrderResult(OrderStatus.FILLED, position_id=position_id, filled_volume=position.volume)

    def cancel_order(self, order_id: str) -> bool:
        return False

    def reconcile(self) -> dict[str, Any]:
        return {"positions": self.get_open_positions(), "orders": [], "timestamp": datetime.now(timezone.utc)}

    def _persist(self) -> None:
        values = []
        for position in self.positions.values():
            raw = position.__dict__.copy()
            raw["direction"] = position.direction.value
            raw["opened_at"] = position.opened_at.isoformat()
            values.append(raw)
        self.store.set_state("paper_positions", values)
        self.store.set_state("paper_balance", self.balance)
        self.store.set_state("paper_realized_today", self.realized_today)
