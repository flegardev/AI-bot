from __future__ import annotations

import copy
import math
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
from core.timeframes import filter_closed_candles, timeframe_to_timedelta
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
        slippage_pct: float = 0.0,
        swap_per_lot_day: float = 0.0,
        persist_state: bool = True,
    ):
        self.store = store
        self.persist_state = persist_state
        self.connected = False
        self.spread = spread
        self.slippage = slippage
        self.slippage_pct = slippage_pct
        self.swap_per_lot_day = swap_per_lot_day
        if any(not math.isfinite(x) or x < 0 for x in (spread, slippage, slippage_pct, commission_per_lot, swap_per_lot_day)):
            raise ValueError("paper costs must be finite and nonnegative")
        self.commission_per_lot = commission_per_lot
        self.simultaneous_policy = simultaneous_policy
        self._symbol_info = symbol_info or SymbolInfo(
            "EURUSD", 0.00001, 1.0, 100_000, 0.01, 100.0, 0.01, leverage=100
        )
        self.balance = float(store.get_state("paper_balance", initial_balance))
        self.realized_today = float(store.get_state("paper_realized_today", 0.0))
        self.day = store.get_state("paper_day")
        self.clock = datetime.now(timezone.utc)
        self.positions: dict[str, Position] = {}
        for raw in store.get_state("paper_positions", []):
            raw["direction"] = Direction(raw["direction"])
            raw["opened_at"] = datetime.fromisoformat(raw["opened_at"])
            self.positions[raw["position_id"]] = Position(**raw)

    def set_clock(self, timestamp: datetime) -> None:
        if timestamp.tzinfo is None:
            raise ValueError("paper clock must have a timezone")
        self.clock = timestamp.astimezone(timezone.utc)
        day = self.clock.date().isoformat()
        if day != self.day:
            self.day, self.realized_today = day, 0.0

    def _cost(self, price: float) -> float:
        return self.slippage + abs(price) * self.slippage_pct

    def _pnl(self, position: Position, price: float) -> float:
        info = self.get_symbol_info(position.symbol)
        multiplier = 1 if position.direction == Direction.LONG else -1
        return (price - position.entry_price) * multiplier * position.volume * info.tick_value / info.tick_size

    def connect(self) -> None:
        self.connected = True
        self.store.record_event("startup", {}, mode="paper")

    def disconnect(self) -> None:
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
            self.balance, self.balance + floating, max(0.0, self.balance + floating - used), used,
            floating, self.realized_today, timestamp=self.clock
        )

    def calculate_position_size(self, request: PositionSizeRequest):
        return calculate_mt5_position_size(request)

    def get_open_positions(self, symbol: str | None = None) -> list[Position]:
        return [p for p in self.positions.values() if symbol is None or p.symbol == symbol]

    def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return []

    def place_order(self, request: OrderRequest) -> OrderResult:
        if not self.persist_state:
            return self._place_order(request)
        previous_state = copy.deepcopy((self.balance, self.realized_today, self.day, self.positions, self.clock))
        try:
            with self.store.atomic():
                self._reload_persisted()
                return self._place_order(request)
        except BaseException:
            self.balance, self.realized_today, self.day, self.positions, self.clock = previous_state
            raise

    def _place_order(self, request: OrderRequest) -> OrderResult:
        previous = self.store.get_idempotent_response(request.client_order_id)
        if previous:
            previous["status"] = OrderStatus(previous["status"])
            return OrderResult(**previous)
        info = self.get_symbol_info(request.signal.symbol)
        reference = request.reference_price if request.reference_price is not None else request.signal.entry
        timestamp = request.execution_timestamp or self.clock
        self.set_clock(timestamp)
        adverse = self._cost(reference) + self.spread / 2
        fill_price = reference + adverse if request.signal.direction == Direction.LONG else reference - adverse
        margin = fill_price * info.contract_size * request.volume / max(info.leverage, 1)
        long = request.signal.direction == Direction.LONG
        valid_protection = (request.signal.stop_loss < fill_price < request.signal.take_profit if long
                            else request.signal.take_profit < fill_price < request.signal.stop_loss)
        if not all(math.isfinite(x) and x > 0 for x in (reference, fill_price, request.volume)):
            result = OrderResult(OrderStatus.REJECTED, message="nonfinite or nonpositive order")
        elif not valid_protection:
            result = OrderResult(OrderStatus.REJECTED, message="gap crossed protection level")
        elif margin + self.commission_per_lot * request.volume > self.get_account_state().free_margin:
            result = OrderResult(OrderStatus.REJECTED, message="insufficient margin")
        elif request.volume < info.volume_min or request.volume > info.volume_max or not math.isclose(request.volume / info.volume_step, round(request.volume / info.volume_step), abs_tol=1e-7):
            result = OrderResult(OrderStatus.REJECTED, message="invalid volume")
        else:
            order_id, position_id = uuid4().hex, uuid4().hex
            entry_fee = self.commission_per_lot * request.volume
            self.balance -= entry_fee
            self.realized_today -= entry_fee
            position = Position(
                position_id, request.signal.symbol, request.signal.direction, request.volume,
                fill_price, request.signal.stop_loss, request.signal.take_profit,
                timestamp, order_id, metadata={**request.signal.metadata,
                    "signal_timestamp": request.signal.timestamp.isoformat(),
                    "signal_id": request.signal.signal_id, "entry_fee": entry_fee,
                    "entry_slippage": self._cost(reference), "entry_spread": self.spread, "reference_price": reference}
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
        if not self.persist_state:
            return self._process_bar(symbol, bar, timestamp)
        before = copy.deepcopy((self.balance, self.realized_today, self.day, self.positions, self.clock))
        try:
            with self.store.atomic():
                return self._process_bar(symbol, bar, timestamp)
        except BaseException:
            self.balance, self.realized_today, self.day, self.positions, self.clock = before
            raise

    def _process_bar(self, symbol: str, bar: pd.Series, timestamp: datetime | None = None) -> list[Fill]:
        """Apply OHLC exits; gaps fill at the adverse open and ambiguous bars stop first."""
        fills: list[Fill] = []
        timestamp = timestamp or datetime.now(timezone.utc)
        self.set_clock(timestamp)
        if not self.positions:
            return fills
        for position in list(self.get_open_positions(symbol)):
            # OHLC bars that began before entry cannot supply post-entry extrema.
            bar_open = pd.Timestamp(bar.name) if isinstance(bar.name, (pd.Timestamp, datetime)) else None
            if bar_open is not None and bar_open < pd.Timestamp(position.opened_at):
                continue
            is_long = position.direction == Direction.LONG
            # Input OHLC is midpoint. Exit longs at bid and shorts at ask.
            offset = -self.spread / 2 if is_long else self.spread / 2
            opened, high, low = (float(bar[key]) + offset for key in ("open", "high", "low"))
            stop_gap = opened <= position.stop_loss if is_long else opened >= position.stop_loss
            tp_gap = opened >= position.take_profit if is_long else opened <= position.take_profit
            stop_touch = low <= position.stop_loss if is_long else high >= position.stop_loss
            tp_touch = high >= position.take_profit if is_long else low <= position.take_profit
            reason = None
            price = 0.0
            if stop_gap:
                reason, price = "stop_gap", opened
            elif tp_gap:
                # Conservative limit execution: no favorable gap improvement.
                reason, price = "take_profit_gap", position.take_profit
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
                fill_time = bar_open.to_pydatetime() if reason.endswith("_gap") and bar_open is not None else timestamp
                fills.append(self._close_at(position, price, reason, fill_time))
            else:
                mark = float(bar["close"]) + offset
                position.metadata["mark_price"] = mark
                position.unrealized_pnl = self._pnl(position, mark)
        self._persist()
        return fills

    def apply_completed_bar(self, symbol: str, bar: pd.Series, timeframe: str, as_of: datetime) -> list[Fill]:
        """Exactly-once persistent replay, including rollback of in-memory state."""
        if not isinstance(bar.name, (pd.Timestamp, datetime)):
            raise ValueError("replay bar requires an opening timestamp")
        opened = pd.Timestamp(bar.name)
        if opened.tzinfo is None or as_of.tzinfo is None:
            raise ValueError("replay timestamps must be timezone aware")
        closed = opened + timeframe_to_timedelta(timeframe)
        if closed > pd.Timestamp(as_of):
            raise ValueError("cannot replay an incomplete candle")
        # Reuse the OHLC geometry validation; do not normalize away source errors.
        validated = filter_closed_candles(pd.DataFrame([bar], index=pd.DatetimeIndex([opened])), timeframe, as_of)
        values = validated[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
        if not all(math.isfinite(float(value)) for value in values.ravel()) or (values[:, :4] <= 0).any() or (values[:, 4] < 0).any():
            raise ValueError("invalid replay OHLCV")
        key = f"paper_last_bar_{symbol}"
        previous_state = copy.deepcopy((self.balance, self.realized_today, self.day, self.positions, self.clock))
        try:
            with self.store.atomic():
                self._reload_persisted()
                last = self.store.get_state(key)
                if last and opened <= pd.Timestamp(last):
                    return []
                fills = self.process_bar(symbol, bar, closed.to_pydatetime())
                self._persist()
                self.store.set_state(key, opened.isoformat())
                return fills
        except BaseException:
            self.balance, self.realized_today, self.day, self.positions, self.clock = previous_state
            raise

    def _reload_persisted(self):
        if not self.persist_state:
            return
        self.balance = float(self.store.get_state("paper_balance", self.balance))
        self.realized_today = float(self.store.get_state("paper_realized_today", self.realized_today))
        self.day = self.store.get_state("paper_day", self.day)
        values = self.store.get_state("paper_positions")
        if values is not None:
            self.positions = {}
            for raw in values:
                raw["direction"] = Direction(raw["direction"])
                raw["opened_at"] = datetime.fromisoformat(raw["opened_at"])
                self.positions[raw["position_id"]] = Position(**raw)

    def _close_at(self, position: Position, price: float, reason: str, timestamp: datetime | None) -> Fill:
        timestamp = timestamp or self.clock
        self.set_clock(timestamp)
        adverse = 0.0 if reason.startswith("take_profit") else self._cost(price)
        fill_price = price - adverse if position.direction == Direction.LONG else price + adverse
        gross = self._pnl(position, fill_price)
        exit_fee = self.commission_per_lot * position.volume
        entry_fee = float(position.metadata.get("entry_fee", 0.0))
        swap = self.swap_per_lot_day * position.volume * max(0, (timestamp.date() - position.opened_at.date()).days)
        commission = entry_fee + exit_fee
        pnl = gross - commission - swap
        self.balance += gross - exit_fee - swap
        self.realized_today += gross - exit_fee - swap
        del self.positions[position.position_id]
        fill = Fill(
            uuid4().hex, position.position_id, position.symbol, position.direction, fill_price,
            position.volume, timestamp, commission, adverse
        )
        self.store.record_event(
            "position_closed", {
                **fill.__dict__,
                "reason": reason,
                "pnl": pnl,
                "swap": swap,
                "features": position.metadata,
                "entry_slippage": position.metadata.get("entry_slippage", 0.0),
                "exit_midpoint": price + (self.spread / 2 if position.direction == Direction.LONG else -self.spread / 2),
                "exit_spread": self.spread,
                "entry_price": position.entry_price,
                "stop_loss": position.stop_loss,
                "take_profit": position.take_profit,
                "volume": position.volume,
                "opened_at": position.opened_at.isoformat(),
            },
            mode="paper", symbol=position.symbol, entity_id=position.position_id
        )
        self._persist()
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
        offset = -self.spread / 2 if position.direction == Direction.LONG else self.spread / 2
        mark = float(position.metadata.get("mark_price", position.metadata.get("reference_price", position.entry_price) + offset))
        self._close_at(position, mark, "manual", None)
        return OrderResult(OrderStatus.FILLED, position_id=position_id, filled_volume=position.volume)

    def cancel_order(self, order_id: str) -> bool:
        return False

    def reconcile(self) -> dict[str, Any]:
        return {"positions": self.get_open_positions(), "orders": [], "timestamp": datetime.now(timezone.utc)}

    def _persist(self) -> None:
        if not self.persist_state:
            return
        values = []
        for position in self.positions.values():
            raw = position.__dict__.copy()
            raw["direction"] = position.direction.value
            raw["opened_at"] = position.opened_at.isoformat()
            values.append(raw)
        with self.store.atomic():
            self.store.set_state("paper_positions", values)
            self.store.set_state("paper_balance", self.balance)
            self.store.set_state("paper_realized_today", self.realized_today)
            self.store.set_state("paper_day", self.day)
