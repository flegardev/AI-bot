from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time, timezone

from core.models import (
    Position,
    PositionSizeRequest,
    RiskDecision,
    SizingResult,
    TradeSignal,
)
from risk.sizing import calculate_mt5_position_size


@dataclass(frozen=True)
class RiskLimits:
    risk_per_trade: float = 0.01
    max_daily_loss_fraction: float = 0.03
    max_drawdown_fraction: float = 0.10
    max_open_positions: int = 1
    max_portfolio_risk_fraction: float = 0.03
    max_symbol_exposure_fraction: float = 1.0
    max_spread: float = float("inf")
    max_slippage: float = float("inf")
    max_price_age_seconds: int = 120
    include_floating_in_daily_loss: bool = True
    session_start_utc: time | None = None
    session_end_utc: time | None = None


class RiskEngine:
    def __init__(self, limits: RiskLimits):
        self.limits = limits

    def evaluate(
        self,
        signal: TradeSignal,
        sizing_request: PositionSizeRequest,
        open_positions: list[Position],
        *,
        equity_high_water: float,
        start_of_day_equity: float,
        spread: float = 0.0,
        price_timestamp: datetime | None = None,
        portfolio_risk: float = 0.0,
        symbol_notional: float = 0.0,
        live_entries_enabled: bool = True,
        sizing: SizingResult | None = None,
        now: datetime | None = None,
    ) -> RiskDecision:
        account = sizing_request.account
        reasons: list[str] = []
        sizing_result = sizing or calculate_mt5_position_size(sizing_request)
        if not live_entries_enabled:
            reasons.append("entries disabled by kill switch")
        if len(open_positions) >= self.limits.max_open_positions:
            reasons.append("maximum open positions reached")
        if not math.isfinite(spread) or spread < 0 or spread > self.limits.max_spread:
            reasons.append("spread exceeds limit")
        observed = price_timestamp or signal.timestamp
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        age = ((now or datetime.now(timezone.utc)) - observed.astimezone(timezone.utc)).total_seconds()
        if age < -5 or age > self.limits.max_price_age_seconds:
            reasons.append("stale price")
        daily_pnl = account.realized_pnl_today
        if self.limits.include_floating_in_daily_loss:
            daily_pnl += account.floating_pnl
        if start_of_day_equity > 0 and daily_pnl <= -(
            start_of_day_equity * self.limits.max_daily_loss_fraction
        ):
            reasons.append("daily loss limit reached")
        elif start_of_day_equity > 0 and account.equity <= start_of_day_equity * (1 - self.limits.max_daily_loss_fraction):
            reasons.append("daily loss limit reached")
        if equity_high_water > 0:
            drawdown = (equity_high_water - account.equity) / equity_high_water
            if drawdown >= self.limits.max_drawdown_fraction:
                reasons.append("maximum drawdown reached")
        if portfolio_risk + sizing_result.estimated_loss_at_stop > account.equity * self.limits.max_portfolio_risk_fraction or portfolio_risk >= account.equity * self.limits.max_portfolio_risk_fraction:
            reasons.append("maximum portfolio risk reached")
        proposed_notional = (
            sizing_request.entry_price
            * sizing_request.symbol_info.contract_size
            * max(sizing_result.volume, 0)
        )
        if symbol_notional + proposed_notional > (
            account.equity * self.limits.max_symbol_exposure_fraction
        ):
            reasons.append("maximum symbol exposure reached")

        if self.limits.session_start_utc and self.limits.session_end_utc:
            current = signal.timestamp.astimezone(timezone.utc).time()
            if not (self.limits.session_start_utc <= current <= self.limits.session_end_utc):
                reasons.append("outside trading session")

        if not sizing_result.accepted:
            reasons.append(sizing_result.rejection_reason or "position sizing rejected")
        return RiskDecision(not reasons, tuple(reasons), sizing_result, signal.signal_id)
