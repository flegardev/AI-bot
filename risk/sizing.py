from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import Callable

from core.models import PositionSizeRequest, SizingResult

ProfitCalculator = Callable[[float, float], float]
MarginCalculator = Callable[[float], float]


def _round_down(value: float, step: float) -> float:
    if step <= 0:
        return 0.0
    units = (Decimal(str(value)) / Decimal(str(step))).to_integral_value(rounding=ROUND_DOWN)
    return float(units * Decimal(str(step)))


def calculate_mt5_position_size(
    request: PositionSizeRequest,
    profit_calculator: ProfitCalculator | None = None,
    margin_calculator: MarginCalculator | None = None,
) -> SizingResult:
    """Risk-capped MT5 sizing using tick economics and optional broker calculators."""
    info = request.symbol_info
    requested_risk = request.account.equity * request.risk_fraction

    def empty(reason: str) -> SizingResult:
        return SizingResult(requested_risk, 0.0, 0.0, 0.0, reason)

    if request.account.equity <= 0 or request.risk_fraction <= 0 or request.risk_fraction > 1:
        return empty("invalid account equity or risk fraction")
    if request.entry_price <= 0 or request.stop_loss <= 0:
        return empty("entry and stop must be positive")
    distance = abs(request.entry_price - request.stop_loss)
    if distance == 0:
        return empty("zero-distance stop")
    if request.direction.value == "LONG" and request.stop_loss >= request.entry_price:
        return empty("long stop must be below entry")
    if request.direction.value == "SHORT" and request.stop_loss <= request.entry_price:
        return empty("short stop must be above entry")
    if request.direction.value == "LONG" and not info.can_long:
        return empty("long direction unavailable")
    if request.direction.value == "SHORT" and not info.can_short:
        return empty("short direction unavailable")
    min_stop_distance = info.stops_level_points * info.point
    if distance + 1e-12 < min_stop_distance:
        return empty("stop violates broker minimum distance")
    if info.tick_size <= 0 or info.tick_value <= 0 or info.volume_step <= 0:
        return empty("invalid symbol tick or volume metadata")

    if profit_calculator:
        loss_per_lot = abs(profit_calculator(request.entry_price, request.stop_loss))
    else:
        loss_per_lot = (distance / info.tick_size) * info.tick_value
    loss_per_lot += max(0.0, request.estimated_commission_per_lot)
    if loss_per_lot <= 0:
        return empty("unable to estimate stop loss")

    raw_volume = min(requested_risk / loss_per_lot, info.volume_max)
    volume = _round_down(raw_volume, info.volume_step)
    if volume < info.volume_min:
        return empty("minimum volume would exceed allowed risk")

    estimated_loss = loss_per_lot * volume
    if estimated_loss > requested_risk + max(1e-8, requested_risk * 1e-9):
        return empty("rounded volume exceeds risk")

    if margin_calculator:
        margin = max(0.0, margin_calculator(volume))
    else:
        leverage = max(info.leverage, 1.0)
        margin = request.entry_price * info.contract_size * volume / leverage
    if margin > request.account.free_margin:
        return SizingResult(
            requested_risk, estimated_loss, 0.0, margin, "insufficient free margin"
        )
    return SizingResult(requested_risk, estimated_loss, volume, margin, None)
