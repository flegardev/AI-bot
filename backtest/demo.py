"""Export demo observation evidence without enabling broker execution."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pandas as pd


def export_demo_report(store, output):
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    # Read all records: summary must not silently truncate long demo sessions.
    with store.connection() as connection:
        rows = connection.execute("SELECT * FROM events ORDER BY id").fetchall()
    events = [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]
    counts = Counter(event["event_type"] for event in events)
    quotes = [event["payload"] for event in events if event["event_type"] == "market_observation"]
    closes = [event["payload"] for event in events if event["event_type"] == "position_closed"]
    snapshots = [event["payload"] for event in events if event["event_type"] == "paper_account_snapshot"]
    configs = {event["payload"]["config_sha256"] for event in events if event["event_type"] == "demo_configuration"}
    pnls = [float(item.get("pnl", 0)) for item in closes]
    values = pd.Series([float(item["equity"]) for item in snapshots], dtype=float)
    drawdown = abs(float((values / values.cummax() - 1).min())) if not values.empty else None
    summary = {"mode": "paper_simulation_using_demo_market_data", "broker_orders_sent": False,
        "event_counts": dict(counts), "configurations_observed": len(configs),
        "configuration_frozen": len(configs) == 1, "quote_observations": len(quotes),
        "valid_quotes": sum(bool(item.get("valid")) for item in quotes),
        "closed_trades": len(closes), "net_pnl": sum(pnls), "maximum_observed_drawdown": drawdown,
        "blocked_cycles": counts["cycle_blocked"],
        "status": "observations_only_no_live_approval",
        "limitations": ["sampled quotes and post-entry completed bars; intrabar paths not known",
            "paper fills are simulated, not actual demo-broker execution evidence",
            "compare realized broker demo fills separately before any live decision"]}
    (root / "demo_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    pd.DataFrame(quotes).to_csv(root / "quote_observations.csv", index=False)
    pd.DataFrame(closes).to_csv(root / "closed_trades.csv", index=False)
    pd.DataFrame(snapshots).to_csv(root / "account_snapshots.csv", index=False)
    return summary
