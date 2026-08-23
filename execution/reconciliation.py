from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

from brokers.base import BrokerAdapter
from core.models import Position
from persistence.store import TradingStore


class ReconciliationService:
    def __init__(self, broker: BrokerAdapter, store: TradingStore):
        self.broker = broker
        self.store = store

    def run(self) -> dict[str, Any]:
        snapshot = self.broker.reconcile()
        positions = cast(list[Position], snapshot.get("positions", []))
        orders = cast(list[dict[str, Any]], snapshot.get("orders", []))
        broker_positions = {item.position_id: item for item in positions}
        prior_ids = set(self.store.get_state("broker_position_ids", []))
        current_ids = set(broker_positions)
        opened = sorted(current_ids - prior_ids)
        closed_or_missing = sorted(prior_ids - current_ids)
        report = {
            "opened": opened,
            "closed_or_missing": closed_or_missing,
            "positions": len(current_ids),
            "orders": len(orders),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        for position_id in opened:
            self.store.record_event(
                "broker_position_discovered", broker_positions[position_id].__dict__,
                symbol=broker_positions[position_id].symbol, entity_id=position_id
            )
        for position_id in closed_or_missing:
            self.store.record_event("broker_position_closed_or_missing", {}, entity_id=position_id)
        self.store.set_state("broker_position_ids", sorted(current_ids))
        self.store.set_state("last_reconciliation", report)
        return report
