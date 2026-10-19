from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from brokers.base import BrokerAdapter, CriticalExecutionError
from core.models import OrderRequest, OrderResult, OrderStatus, RiskDecision, TradeSignal
from persistence.store import TradingStore


class SafeExecutionService:
    """Idempotent execution with mandatory post-fill protection verification."""

    def __init__(self, broker: BrokerAdapter, store: TradingStore, cooldown_minutes: int = 60):
        self.broker = broker
        self.store = store
        self.cooldown_minutes = cooldown_minutes

    @property
    def entries_enabled(self) -> bool:
        disabled_until = self.store.get_state("entries_disabled_until")
        if not disabled_until:
            return True
        return datetime.now(timezone.utc) >= datetime.fromisoformat(disabled_until)

    def reset_kill_switch(self) -> None:
        self.store.set_state("entries_disabled_until", None)

    def kill_switch(self, cancel_pending_entries: bool = False) -> None:
        self.store.set_state("entries_disabled_until", "9999-12-31T23:59:59+00:00")
        if cancel_pending_entries:
            for order in self.broker.get_open_orders():
                order_id = str(order.get("ticket") or order.get("id") or "")
                if order_id:
                    self.broker.cancel_order(order_id)

    def execute(self, signal: TradeSignal, decision: RiskDecision, *, max_slippage: float = 0.0,
                reference_price: float | None = None, execution_timestamp: datetime | None = None) -> Any:
        if not self.entries_enabled:
            raise CriticalExecutionError("Live entries are disabled by the execution kill switch")
        if not decision.approved or not decision.sizing or not decision.sizing.accepted:
            self.store.record_event(
                "risk_rejection", {"reasons": decision.reasons}, symbol=signal.symbol,
                entity_id=signal.signal_id
            )
            return None
        client_id = f"{signal.signal_id[:20]}-{signal.timestamp:%Y%m%d%H%M}"
        previous = self.store.get_idempotent_response(client_id)
        if previous:
            previous["status"] = OrderStatus(previous["status"])
            return OrderResult(**previous)
        if not self.store.claim_entry(client_id):
            self.kill_switch()
            raise CriticalExecutionError("Unresolved entry exists; reconcile broker state before resuming")
        request = OrderRequest(signal, decision.sizing.volume, client_id,
                               max_slippage=max_slippage, reference_price=reference_price,
                               execution_timestamp=execution_timestamp)
        self.store.record_event("order_request", request.__dict__, symbol=signal.symbol, entity_id=client_id)
        try:
            result = self.broker.place_order(request)
        except Exception as exc:
            self.kill_switch()
            self.store.record_event("ambiguous_entry", {"client_id": client_id, "error": str(exc)},
                                    symbol=signal.symbol, entity_id=client_id)
            raise CriticalExecutionError("Broker entry outcome unknown; entries disabled for reconciliation") from exc
        self.store.save_idempotent_response(
            client_id, {**result.__dict__, "status": result.status.value}
        )
        self.store.record_event("broker_response", result.__dict__, symbol=signal.symbol, entity_id=client_id)
        if result.status not in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            self.store.set_state("pending_entry", None)
            return result

        try:
            protected, position = self._verify_protection(signal, result)
        except Exception as exc:
            protected, position = False, None
            self.store.record_event("protection_verification_failed", {"error": str(exc)},
                                    symbol=signal.symbol, entity_id=result.position_id)
        if not protected:
            disabled_until = datetime.now(timezone.utc) + timedelta(minutes=self.cooldown_minutes)
            self.store.set_state("entries_disabled_until", disabled_until.isoformat())
            try:
                close_result = self.broker.close_position(
                    position.position_id if position else result.position_id
                )
            except Exception as exc:
                close_result = OrderResult(OrderStatus.REJECTED, message=f"emergency close failed: {exc}")
            incident = {
                "signal_id": signal.signal_id,
                "order_result": result.__dict__,
                "emergency_close": close_result.__dict__,
            }
            self.store.record_event(
                "critical_unprotected_position", incident, symbol=signal.symbol,
                entity_id=result.position_id
            )
            raise CriticalExecutionError(
                "Entry filled without verified SL/TP; emergency close attempted and entries disabled"
            )
        self.store.set_state("pending_entry", None)
        return result

    def _verify_protection(self, signal, result):
        positions = self.broker.get_open_positions(signal.symbol)
        position = next(
            (item for item in positions if item.position_id == result.position_id),
            None,
        )
        protected = bool(
            position
            and abs(position.stop_loss - signal.stop_loss) <= 1e-8
            and abs(position.take_profit - signal.take_profit) <= 1e-8
        )
        if not protected and position:
            protected = self.broker.modify_position(
                position.position_id, signal.stop_loss, signal.take_profit
            )
            if protected:
                refreshed = [
                    item for item in self.broker.get_open_positions(signal.symbol)
                    if item.position_id == position.position_id
                ]
                protected = bool(
                    refreshed
                    and abs(refreshed[0].stop_loss - signal.stop_loss) <= 1e-8
                    and abs(refreshed[0].take_profit - signal.take_profit) <= 1e-8
                )
        return protected, position
