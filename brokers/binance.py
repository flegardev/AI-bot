from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from brokers.base import BrokerCapabilities, BrokerError
from core.models import AccountState, Direction, OrderRequest, OrderResult, OrderStatus, Position, SymbolInfo
from core.timeframes import filter_closed_candles
from data.fetcher import create_exchange


class BinanceBrokerAdapter:
    """Isolated Binance spot adapter. Spot capability is deliberately long-only."""

    capabilities = BrokerCapabilities(long=True, short=False, native_protection=True)

    def __init__(self, exchange: Any | None = None, sandbox: bool = True):
        self.exchange: Any = exchange
        self.sandbox = sandbox
        self.connected = False

    def connect(self) -> None:
        if self.exchange is None:
            self.exchange = create_exchange(include_credentials=True, sandbox=self.sandbox)
        self.exchange.load_markets()
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def get_candles(self, symbol: str, timeframe: str, start=None, end=None) -> pd.DataFrame:
        since = int(start.timestamp() * 1000) if start else None
        end_ms = int(end.timestamp() * 1000) if end else None
        raw: list[list[float]] = []
        while True:
            page = self.exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
            if not page:
                break
            raw.extend(page)
            if end_ms is None or page[-1][0] >= end_ms or len(page) < 1000:
                break
            next_since = int(page[-1][0]) + 1
            if since is not None and next_since <= since:
                break
            since = next_since
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        result = filter_closed_candles(df.set_index("timestamp"), timeframe, end)
        return result.loc[result.index < pd.Timestamp(end)] if end else result

    def get_account_state(self) -> AccountState:
        balance = self.exchange.fetch_balance()
        total = float(balance.get("total", {}).get("USDT", 0.0))
        free = float(balance.get("free", {}).get("USDT", 0.0))
        return AccountState(total, total, free, max(0.0, total - free))

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        market = self.exchange.market(symbol)
        limits = market.get("limits", {})
        precision = market.get("precision", {}).get("amount", 8)
        step = 10 ** (-precision) if isinstance(precision, int) else float(precision)
        return SymbolInfo(symbol, market.get("precision", {}).get("price", 1e-8), 1.0, 1.0,
                          limits.get("amount", {}).get("min") or step,
                          limits.get("amount", {}).get("max") or float("inf"), step,
                          leverage=1, can_short=False)

    def get_open_positions(self, symbol=None) -> list[Position]:
        return []

    def get_open_orders(self, symbol=None) -> list[dict[str, Any]]:
        return self.exchange.fetch_open_orders(symbol)

    def calculate_position_size(self, request):
        from risk.sizing import calculate_mt5_position_size
        return calculate_mt5_position_size(request)

    def place_order(self, request: OrderRequest) -> OrderResult:
        if request.signal.direction == Direction.SHORT:
            return OrderResult(OrderStatus.REJECTED, message="Binance spot is long-only")
        try:
            entry = self.exchange.create_order(
                request.signal.symbol, "market", "buy", request.volume, None,
                {"newClientOrderId": request.client_order_id}
            )
        except Exception as exc:
            return OrderResult(OrderStatus.REJECTED, message=str(exc))
        # Binance spot OCO support varies by ccxt/version. Refuse to claim safety
        # unless both native exits are accepted by the exchange.
        try:
            self.exchange.create_order(
                request.signal.symbol, "limit", "sell", request.volume, request.signal.take_profit,
                {"stopPrice": request.signal.stop_loss, "type": "OCO"}
            )
        except Exception as exc:
            try:
                self.exchange.create_order(request.signal.symbol, "market", "sell", request.volume)
            finally:
                raise BrokerError(f"Entry was closed because protective OCO failed: {exc}") from exc
        return OrderResult(OrderStatus.FILLED, str(entry["id"]), str(entry["id"]), request.volume,
                           float(entry.get("average") or request.signal.entry))

    def modify_position(self, position_id, stop_loss, take_profit) -> bool:
        return False

    def close_position(self, position_id) -> OrderResult:
        return OrderResult(OrderStatus.REJECTED, message="spot position requires symbol/amount")

    def cancel_order(self, order_id: str) -> bool:
        self.exchange.cancel_order(order_id)
        return True

    def reconcile(self) -> dict[str, Any]:
        return {"positions": [], "orders": self.get_open_orders(), "timestamp": datetime.utcnow()}
