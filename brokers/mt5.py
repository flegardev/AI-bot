from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

import config
from brokers.base import BrokerCapabilities, BrokerError
from core.models import (
    AccountState,
    Direction,
    OrderRequest,
    OrderResult,
    OrderStatus,
    Position,
    PositionSizeRequest,
    SymbolInfo,
)
from core.quotes import Quote
from core.timeframes import filter_closed_candles
from data.contracts import MarketDataContract
from risk.sizing import calculate_mt5_position_size

TIMEFRAMES = {
    "1m": "TIMEFRAME_M1", "5m": "TIMEFRAME_M5", "15m": "TIMEFRAME_M15",
    "1h": "TIMEFRAME_H1", "4h": "TIMEFRAME_H4", "1d": "TIMEFRAME_D1",
}


class MT5BrokerAdapter:
    capabilities = BrokerCapabilities(long=True, short=True, native_protection=True)

    def __init__(self, mt5_module: Any | None = None):
        if mt5_module is None:
            try:
                import MetaTrader5 as imported_mt5
            except ImportError as exc:
                raise BrokerError("MetaTrader5 package is not installed") from exc
            mt5_module = imported_mt5
        self.mt5: Any = mt5_module
        self.connected = False
        self.magic = int(os.getenv("MT5_MAGIC_NUMBER", "260727"))
        self.source_timezone = os.getenv("MT5_SOURCE_TIMEZONE", "UTC")

    def _source_timestamp(self, raw_seconds):
        stamp = pd.Timestamp(raw_seconds, unit="s")
        return stamp.tz_localize(self.source_timezone, ambiguous="raise", nonexistent="raise").tz_convert("UTC")

    def _request_time(self, timestamp):
        if timestamp is None or self.source_timezone == "UTC":
            return timestamp
        return pd.Timestamp(timestamp).tz_convert(self.source_timezone).tz_localize(None).tz_localize("UTC").to_pydatetime()

    def connect(self) -> None:
        path = os.getenv("MT5_TERMINAL_PATH") or None
        kwargs: dict[str, Any] = {"path": path} if path else {}
        if not self.mt5.initialize(**kwargs):
            raise BrokerError(f"MT5 initialize failed: {self.mt5.last_error()}")
        login = os.getenv("MT5_LOGIN", "").strip()
        if login:
            if not self.mt5.login(
                int(login), password=os.getenv("MT5_PASSWORD", ""), server=os.getenv("MT5_SERVER", "")
            ):
                self.mt5.shutdown()
                raise BrokerError(f"MT5 login failed: {self.mt5.last_error()}")
        if self.mt5.account_info() is None:
            self.mt5.shutdown()
            raise BrokerError(f"MT5 account unavailable: {self.mt5.last_error()}")
        self.connected = True

    def disconnect(self) -> None:
        self.mt5.shutdown()
        self.connected = False

    def _ensure(self) -> None:
        if self.connected:
            terminal = self.mt5.terminal_info()
            account = self.mt5.account_info()
            if terminal is not None and account is not None:
                return
            self.mt5.shutdown()
            self.connected = False
        self.connect()

    def _select_symbol(self, symbol: str) -> Any:
        self._ensure()
        info = self.mt5.symbol_info(symbol)
        if info is None:
            raise BrokerError(f"MT5 symbol not found: {symbol}")
        if not info.visible and not self.mt5.symbol_select(symbol, True):
            raise BrokerError(f"MT5 symbol cannot be selected: {symbol}")
        return self.mt5.symbol_info(symbol)

    def get_candles(
        self, symbol: str, timeframe: str, start: datetime | None = None, end: datetime | None = None
    ) -> pd.DataFrame:
        self._select_symbol(symbol)
        attr = TIMEFRAMES.get(timeframe.lower())
        if not attr or not hasattr(self.mt5, attr):
            raise BrokerError(f"Unsupported MT5 timeframe: {timeframe}")
        tf = getattr(self.mt5, attr)
        if start:
            rates = self.mt5.copy_rates_range(symbol, tf, self._request_time(start), self._request_time(end or datetime.now(timezone.utc)))
        else:
            rates = self.mt5.copy_rates_from_pos(symbol, tf, 0, 1000)
        if rates is None:
            raise BrokerError(f"MT5 candle retrieval failed: {self.mt5.last_error()}")
        df = pd.DataFrame(rates)
        if df.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        raw_times = pd.DatetimeIndex(pd.to_datetime(df["time"], unit="s"))
        df["timestamp"] = raw_times.tz_localize(self.source_timezone, ambiguous="raise", nonexistent="raise").tz_convert("UTC")
        df = df.set_index("timestamp").rename(columns={"tick_volume": "volume"})
        if df.index.has_duplicates or not df.index.is_monotonic_increasing:
            raise BrokerError("MT5 returned duplicate or unordered candles")
        values = df[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values[:, :4] <= 0).any() or (values[:, 4] < 0).any():
            raise BrokerError("MT5 returned invalid OHLCV")
        output = filter_closed_candles(df[["open", "high", "low", "close", "volume"]], timeframe,
                                      min(end, datetime.now(timezone.utc)) if end else None)
        if start is not None:
            output = output.loc[output.index >= pd.Timestamp(start)]
        output.attrs["data_contract"] = MarketDataContract(
            source="MT5 terminal", timestamp_basis="utc", price_basis="bid", calendar="fx_ny").__dict__
        output.attrs["source_timezone"] = self.source_timezone
        return output

    def get_account_state(self) -> AccountState:
        self._ensure()
        account = self.mt5.account_info()
        if account is None:
            raise BrokerError(f"MT5 account_info failed: {self.mt5.last_error()}")
        return AccountState(
            balance=float(account.balance),
            equity=float(account.equity),
            free_margin=float(account.margin_free),
            used_margin=float(account.margin),
            floating_pnl=float(account.profit),
            currency=str(account.currency),
        )

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        info = self._select_symbol(symbol)
        account = self.mt5.account_info()
        trade_mode = getattr(info, "trade_mode", 0)
        disabled = getattr(self.mt5, "SYMBOL_TRADE_MODE_DISABLED", -1)
        return SymbolInfo(
            symbol=symbol,
            tick_size=float(info.trade_tick_size or info.point),
            tick_value=float(info.trade_tick_value_loss or info.trade_tick_value),
            contract_size=float(info.trade_contract_size),
            volume_min=float(info.volume_min),
            volume_max=float(info.volume_max),
            volume_step=float(info.volume_step),
            stops_level_points=float(info.trade_stops_level),
            point=float(info.point),
            leverage=float(getattr(account, "leverage", 1) or 1),
            currency_profit=str(getattr(info, "currency_profit", "")),
            can_long=trade_mode != disabled,
            can_short=trade_mode != disabled,
        )

    def calculate_position_size(self, request: PositionSizeRequest):
        order_type = self.mt5.ORDER_TYPE_BUY if request.direction == Direction.LONG else self.mt5.ORDER_TYPE_SELL

        def loss(entry: float, stop: float) -> float:
            value = self.mt5.order_calc_profit(order_type, request.symbol, 1.0, entry, stop)
            return abs(float(value)) if value is not None else 0.0

        def margin(volume: float) -> float:
            value = self.mt5.order_calc_margin(order_type, request.symbol, volume, request.entry_price)
            return float(value) if value is not None else float("inf")

        return calculate_mt5_position_size(request, loss, margin)

    def get_open_positions(self, symbol: str | None = None) -> list[Position]:
        self._ensure()
        raw = self.mt5.positions_get(symbol=symbol) if symbol else self.mt5.positions_get()
        if raw is None:
            raise BrokerError(f"MT5 positions_get failed: {self.mt5.last_error()}")
        result = []
        for item in raw:
            direction = Direction.LONG if item.type == self.mt5.POSITION_TYPE_BUY else Direction.SHORT
            result.append(
                Position(
                    str(item.ticket), item.symbol, direction, float(item.volume), float(item.price_open),
                    float(item.sl), float(item.tp), datetime.fromtimestamp(item.time, timezone.utc),
                    status="open", unrealized_pnl=float(item.profit)
                )
            )
        return result

    def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        self._ensure()
        orders = self.mt5.orders_get(symbol=symbol) if symbol else self.mt5.orders_get()
        if orders is None:
            raise BrokerError(f"MT5 orders_get failed: {self.mt5.last_error()}")
        return [item._asdict() for item in orders]

    def _send(self, request: dict[str, Any]) -> Any:
        result = self.mt5.order_send(request)
        if result is None:
            raise BrokerError(f"MT5 order_send failed: {self.mt5.last_error()}")
        return result

    def get_quote(self, symbol: str) -> Quote:
        self._select_symbol(symbol)
        tick = self.mt5.symbol_info_tick(symbol)
        return self._quote_from_tick(tick)

    def _quote_from_tick(self, tick) -> Quote:
        if tick is None or not getattr(tick, "time", 0) or not math.isfinite(float(tick.time)) or float(tick.time) <= 0:
            raise BrokerError("MT5 quote unavailable or missing timestamp")
        return Quote(float(tick.bid), float(tick.ask), self._source_timestamp(tick.time).to_pydatetime(warn=False))

    def place_order(self, request: OrderRequest) -> OrderResult:
        signal = request.signal
        self._select_symbol(signal.symbol)
        try:
            quote = self.get_quote(signal.symbol)
            quote.validate(config.SETTINGS.stale_price_seconds, config.SETTINGS.max_spread)
        except (ValueError, BrokerError) as exc:
            return OrderResult(OrderStatus.REJECTED, message=str(exc))
        is_buy = signal.direction == Direction.LONG
        price = quote.ask if is_buy else quote.bid
        reference = request.reference_price if request.reference_price is not None else signal.entry
        if abs(price - reference) > request.max_slippage:
            return OrderResult(OrderStatus.REJECTED, message="execution price moved beyond slippage limit")
        if not (signal.stop_loss < price < signal.take_profit if is_buy else signal.take_profit < price < signal.stop_loss):
            return OrderResult(OrderStatus.REJECTED, message="execution price crossed SL/TP")
        info = self.mt5.symbol_info(signal.symbol)
        mt5_request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": signal.symbol,
            "volume": request.volume,
            "type": self.mt5.ORDER_TYPE_BUY if is_buy else self.mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": signal.stop_loss,
            "tp": signal.take_profit,
            "deviation": max(0, int(request.max_slippage / info.point)),
            "magic": self.magic,
            "comment": request.client_order_id[:31],
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_IOC,
        }
        check = self.mt5.order_check(mt5_request)
        if check is None or check.retcode != 0:
            return OrderResult(OrderStatus.REJECTED, message=f"MT5 order_check: {check}")
        result = self._send(mt5_request)
        accepted = {
            self.mt5.TRADE_RETCODE_DONE,
            getattr(self.mt5, "TRADE_RETCODE_DONE_PARTIAL", -1),
        }
        if result.retcode not in accepted:
            return OrderResult(OrderStatus.REJECTED, message=str(result.comment), raw=result._asdict())
        status = (
            OrderStatus.PARTIALLY_FILLED
            if result.retcode == getattr(self.mt5, "TRADE_RETCODE_DONE_PARTIAL", -1)
            else OrderStatus.FILLED
        )
        return OrderResult(
            status, str(result.order), str(result.order), float(result.volume),
            float(result.price), str(result.comment), result._asdict()
        )

    def modify_position(self, position_id: str, stop_loss: float, take_profit: float) -> bool:
        result = self._send({
            "action": self.mt5.TRADE_ACTION_SLTP, "position": int(position_id),
            "sl": stop_loss, "tp": take_profit, "magic": self.magic
        })
        return result.retcode == self.mt5.TRADE_RETCODE_DONE

    def close_position(self, position_id: str) -> OrderResult:
        positions = [p for p in self.get_open_positions() if p.position_id == str(position_id)]
        if not positions:
            return OrderResult(OrderStatus.REJECTED, message="position not found")
        position = positions[0]
        tick = self.mt5.symbol_info_tick(position.symbol)
        is_long = position.direction == Direction.LONG
        result = self._send({
            "action": self.mt5.TRADE_ACTION_DEAL, "position": int(position_id),
            "symbol": position.symbol, "volume": position.volume,
            "type": self.mt5.ORDER_TYPE_SELL if is_long else self.mt5.ORDER_TYPE_BUY,
            "price": float(tick.bid if is_long else tick.ask), "deviation": 50,
            "magic": self.magic, "comment": "emergency-close",
            "type_time": self.mt5.ORDER_TIME_GTC, "type_filling": self.mt5.ORDER_FILLING_IOC,
        })
        status = OrderStatus.FILLED if result.retcode == self.mt5.TRADE_RETCODE_DONE else OrderStatus.REJECTED
        return OrderResult(status, str(result.order), str(position_id), float(result.volume), float(result.price), str(result.comment))

    def cancel_order(self, order_id: str) -> bool:
        result = self._send({"action": self.mt5.TRADE_ACTION_REMOVE, "order": int(order_id)})
        return result.retcode == self.mt5.TRADE_RETCODE_DONE

    def reconcile(self) -> dict[str, Any]:
        return {
            "positions": self.get_open_positions(),
            "orders": self.get_open_orders(),
            "timestamp": datetime.now(timezone.utc),
        }

    def check_connection(self, symbol: str) -> dict[str, Any]:
        """Read-only dry-run validation; never submits an order."""
        checks: dict[str, Any] = {"package": True, "terminal": False, "account": False, "symbol": False}
        self.connect()
        checks["terminal"] = bool(self.mt5.terminal_info())
        checks["account"] = self.get_account_state().__dict__
        info = self.get_symbol_info(symbol)
        tick = self.mt5.symbol_info_tick(symbol)
        checks["symbol"] = info.__dict__
        checks["tick"] = tick._asdict() if tick else None
        checks["trading_enabled"] = info.can_long or info.can_short
        observed = datetime.now(timezone.utc)
        checks.update(observed_at=observed.isoformat(), source_timezone=self.source_timezone,
                      valid_spread=False, fresh_tick=False, price_age_seconds=None, quote_errors=[])
        try:
            # Reuse serving normalization and validation; never clamp away clock errors.
            quote = self._quote_from_tick(tick)
            checks["source_timestamp_utc"] = quote.timestamp.isoformat()
            checks["spread"] = quote.spread
            checks["price_age_seconds"] = quote.age_seconds(observed)
            try:
                quote.validate_prices(config.SETTINGS.max_spread)
                checks["valid_spread"] = True
            except ValueError as exc:
                checks["quote_errors"].append(str(exc))
            try:
                quote.validate_timestamp(config.SETTINGS.stale_price_seconds, observed)
                checks["fresh_tick"] = True
            except ValueError as exc:
                checks["quote_errors"].append(str(exc))
        except Exception as exc:
            # Includes invalid/ambiguous source timezone timestamps; diagnostic fails closed.
            checks["quote_errors"].append(str(exc))
        checks["ok"] = all((
            checks["terminal"], checks["account"], checks["symbol"], checks["tick"],
            checks["trading_enabled"], checks["valid_spread"], checks["fresh_tick"],
        ))
        return checks
