"""Bounded train/validation selection with a reporting-only final evaluation."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.research import block_bootstrap
from core.provenance import configuration, git_revision, source_hash
from data.quality import DataQualityError, inspect_dataset
from strategy.competition import candidate

DEFAULT_CANDIDATES = (
    {"name": "no_trade", "period": 20},
    {"name": "smc_baseline_v1", "period": 20},
    {"name": "trend_momentum_v1", "period": 20},
    {"name": "trend_momentum_v1", "period": 30},
    {"name": "atr_breakout_v1", "period": 20},
    {"name": "atr_breakout_v1", "period": 30},
)


def competition(frames, symbol, info, start, end, output, calendar="fx_ny", *,
                candidates=DEFAULT_CANDIDATES, max_experiments=48, min_trades=30):
    specs = [dict(item) for item in candidates]
    for spec in specs:
        if set(spec) != {"name", "period"} or spec["period"] not in {10, 20, 30, 40, 60}:
            raise ValueError("candidate outside declared parameter space")
        candidate(**spec)
    # Train, validation, selected final, flat/rule references, cost and parameter stress.
    if not specs or len(specs) > 10 or 4 * len(specs) + 9 > max_experiments or max_experiments > 100:
        raise ValueError("fixed experiment budget cannot accommodate declared protocol")
    root = Path(output) / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8])
    root.mkdir(parents=True, exist_ok=False)
    quality = inspect_dataset(frames, start, end, calendar, True)
    (root / "data_quality.json").write_text(json.dumps(quality, indent=2), encoding="utf-8")
    protocol = {"schema": "competition-v1", "candidates": specs, "budget": max_experiments,
        "min_validation_trades": min_trades, "seed": 42, "calendar": calendar,
        "start": str(start), "end": str(end), "split": [0.6, 0.2, 0.2],
        "selection": "positive validation net return, minimum sample count, then validation return",
        "git_sha": git_revision(), "source_sha256": source_hash(), "configuration": configuration(),
        "symbol_info": asdict(info),
        "data_digests": {tf: q.get("sha256") for tf, q in quality["timeframes"].items()}}
    protocol_id = hashlib.sha256(json.dumps(protocol, sort_keys=True).encode()).hexdigest()
    (root / "protocol.json").write_text(json.dumps({"sha256": protocol_id, **protocol}, indent=2), encoding="utf-8")
    attempts = []

    def register(row):
        attempts.append(row)
        with (root / "experiments.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"protocol_id": protocol_id, **row}, default=str) + "\n")

    if not quality["ok"]:
        register({"status": "blocked", "reason": "invalid_data"})
        raise DataQualityError(f"competition blocked: {root / 'data_quality.json'}")
    cache = root / "data"
    cache.mkdir()
    for tf, frame in frames.items():
        frame.to_csv(cache / f"{tf}.csv", index_label="timestamp")
    (cache / "symbol.json").write_text(json.dumps(asdict(info), indent=2), encoding="utf-8")
    first_contract = next(iter(frames.values())).attrs.get("data_contract")
    if first_contract:
        (cache / "contract.json").write_text(json.dumps(first_contract, indent=2), encoding="utf-8")
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    train_end = (start + (end - start) * .6).floor("5min")
    selection_end = (start + (end - start) * .8).floor("5min")

    def run(spec, begin, stop, phase, cost=1.0):
        if len(attempts) >= max_experiments:
            raise ValueError("experiment budget exhausted")
        path = root / f"{len(attempts):03d}-{phase}-{spec['name']}-{spec['period']}-cost{cost}"
        try:
            replay = BacktestEngine(symbol, symbol_info=info, strategy=candidate(**spec), artifact_dir=path, cost_multiplier=cost)
            result = replay.run(data=frames, start=begin, end=stop, strict_data=True, calendar=calendar)
            register({"status": "completed", "candidate": spec, "phase": phase, "start": str(begin),
                      "end": str(stop), "cost_multiplier": cost, "stats": result["stats"], "path": str(path)})
            return result
        except (ValueError, RuntimeError) as exc:
            register({"status": "failed", "candidate": spec, "phase": phase, "error": str(exc)})
            return None

    rolling = []
    for index, fraction in enumerate((.3, .45)):
        stop = (start + (end - start) * fraction).floor("5min")
        forward_end = (start + (end - start) * (fraction + .15)).floor("5min")
        training = [(spec, run(spec, start, stop, f"walk{index}_training")) for spec in specs]
        eligible = [(spec, result) for spec, result in training if result and result["stats"]["total_trades"] >= min_trades
                    and result["stats"]["net_return"] > 0]
        winner = max(eligible, key=lambda pair: pair[1]["stats"]["net_return"])[0] if eligible else None
        result = run(winner, stop, forward_end, f"walk{index}_forward") if winner else None
        rolling.append({"train_end": str(stop), "forward_end": str(forward_end), "selected": winner,
                        "forward_stats": result["stats"] if result else None})
    validations = []
    for spec in specs:
        run(spec, start, train_end, "training")
        result = run(spec, train_end, selection_end, "validation")
        if result is not None:
            validations.append((spec, result))
    viable = [(spec, result) for spec, result in validations
              if result["stats"]["net_return"] > 0 and result["stats"]["total_trades"] >= min_trades]
    if not validations:
        raise DataQualityError(f"all validation trials failed; inspect {root / 'experiments.jsonl'}")
    selected = max(viable, key=lambda item: item[1]["stats"]["net_return"])[0] if viable else None
    registry = Path(output) / "holdout_registry.jsonl"
    fingerprint = hashlib.sha256(json.dumps(protocol["data_digests"], sort_keys=True).encode()).hexdigest()
    previous = [json.loads(line) for line in registry.read_text(encoding="utf-8").splitlines()] if registry.exists() else []
    reused = any(row["data_fingerprint"] == fingerprint for row in previous)
    # Selection is persisted before any final-period values are computed.
    freeze = {"protocol_id": protocol_id, "selected": selected, "selection_end": str(selection_end),
              "forward_start": str(selection_end), "forward_end": str(end)}
    (root / "selection.json").write_text(json.dumps(freeze, indent=2), encoding="utf-8")
    final, stress = None, []
    if selected:
        with registry.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"data_fingerprint": fingerprint, "protocol_id": protocol_id,
                "selected": selected, "at": str(datetime.now(timezone.utc))}) + "\n")
        final = run(selected, selection_end, end, "final_evaluation")
        for cost in (.75, 1.5, 2.0):
            result = run(selected, selection_end, end, "final_cost_stress", cost)
            stress.append({"cost_multiplier": cost, "stats": result["stats"] if result else None})
        neighbor = dict(selected, period=30 if selected["period"] == 20 else 20)
        run(neighbor, train_end, selection_end, "validation_parameter_neighbor")
    flat = run({"name": "no_trade", "period": 20}, selection_end, end, "final_flat_reference")
    summary = {"protocol_id": protocol_id, "output": str(root), "selected": selected,
        "validation_ranking": [{"candidate": spec, "stats": result["stats"]} for spec, result in validations],
        "final_evaluation": final["stats"] if final else None, "final_flat": flat["stats"] if flat else None,
        "cost_stress": stress, "experiments": len(attempts), "budget": max_experiments,
        "walk_forward": rolling,
        "uncertainty": block_bootstrap([t["pnl"] for t in final["trades"]]) if final else {"status": "no_selected_candidate"},
        "status": "unproven" if selected else "rejected_no_validation_edge",
        "paper_only": True, "selection_never_uses_final_results": True,
        "final_evaluation_previously_accessed": reused,
        "holdout_note": "local reuse detection only; changing registry directories cannot make seen data untouched"}
    (root / "competition_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
