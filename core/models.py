from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TradingMode(str, Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Candle:
    symbol: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class TradeSignal:
    symbol: str
    direction: Direction
    entry: float
    stop_loss: float
    take_profit: float
    timestamp: datetime
    reason: str
    signal_id: str = field(default_factory=lambda: uuid4().hex)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SymbolInfo:
    symbol: str
    tick_size: float
    tick_value: float
    contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    stops_level_points: float = 0.0
    point: float = 0.0
    leverage: float = 1.0
    currency_profit: str = ""
    can_long: bool = True
    can_short: bool = True


@dataclass
class AccountState:
    balance: float
    equity: float
    free_margin: float
    used_margin: float
    floating_pnl: float = 0.0
    realized_pnl_today: float = 0.0
    currency: str = "USD"
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class PositionSizeRequest:
    symbol: str
    direction: Direction
    entry_price: float
    stop_loss: float
    risk_fraction: float
    account: AccountState
    symbol_info: SymbolInfo
    estimated_commission_per_lot: float = 0.0


@dataclass(frozen=True)
class SizingResult:
    requested_risk: float
    estimated_loss_at_stop: float
    volume: float
    estimated_margin: float
    rejection_reason: str | None = None

    @property
    def accepted(self) -> bool:
        return self.rejection_reason is None and self.volume > 0


@dataclass(frozen=True)
class OrderRequest:
    signal: TradeSignal
    volume: float
    client_order_id: str
    max_slippage: float = 0.0
    order_type: str = "market"
    reference_price: float | None = None
    execution_timestamp: datetime | None = None


@dataclass
class Fill:
    order_id: str
    position_id: str
    symbol: str
    direction: Direction
    price: float
    volume: float
    timestamp: datetime
    commission: float = 0.0
    slippage: float = 0.0


@dataclass
class Position:
    position_id: str
    symbol: str
    direction: Direction
    volume: float
    entry_price: float
    stop_loss: float
    take_profit: float
    opened_at: datetime
    order_id: str = ""
    status: str = "open"
    unrealized_pnl: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderResult:
    status: OrderStatus
    order_id: str = ""
    position_id: str = ""
    filled_volume: float = 0.0
    fill_price: float = 0.0
    message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reasons: tuple[str, ...]
    sizing: SizingResult | None = None
    signal_id: str = ""


def to_dict(value: Any) -> dict[str, Any]:
    data = asdict(value)
    for key, item in list(data.items()):
        if isinstance(item, Enum):
            data[key] = item.value
        elif isinstance(item, datetime):
            data[key] = item.isoformat()
    return data
