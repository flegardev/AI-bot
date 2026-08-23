from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import pandas as pd

from core.models import AccountState, OrderRequest, OrderResult, Position, SizingResult, SymbolInfo


class BrokerError(RuntimeError):
    pass


class CriticalExecutionError(BrokerError):
    pass


@dataclass(frozen=True)
class BrokerCapabilities:
    long: bool = True
    short: bool = True
    native_protection: bool = True
    partial_fills: bool = False


class BrokerAdapter(Protocol):
    capabilities: BrokerCapabilities

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def get_candles(
        self, symbol: str, timeframe: str, start: datetime | None = None, end: datetime | None = None
    ) -> pd.DataFrame: ...
    def get_account_state(self) -> AccountState: ...
    def get_symbol_info(self, symbol: str) -> SymbolInfo: ...
    def get_open_positions(self, symbol: str | None = None) -> list[Position]: ...
    def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]: ...
    def calculate_position_size(self, request: Any) -> SizingResult: ...
    def place_order(self, request: OrderRequest) -> OrderResult: ...
    def modify_position(self, position_id: str, stop_loss: float, take_profit: float) -> bool: ...
    def close_position(self, position_id: str) -> OrderResult: ...
    def cancel_order(self, order_id: str) -> bool: ...
    def reconcile(self) -> dict[str, Any]: ...
