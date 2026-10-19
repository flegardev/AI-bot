"""Reproducible strategy comparisons with rolling selection and a final holdout."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd

import config
from backtest.engine import BacktestEngine
from brokers.base import BrokerCapabilities
from core.strategy_engine import StrategyEngine
from data.quality import DataQualityError, inspect_dataset
from ml.predictor import MLPredictor
from strategy.baselines import BaselineStrategy


def block_bootstrap(pnls, samples=2000, seed=42):
    values = np.asarray(pnls, dtype=float)
    if len(values) < 30:
        return {"status": "insufficient_trades", "trades": len(values), "required": 30}
    rng = np.random.default_rng(seed)
    block = max(2, int(np.sqrt(len(values))))
    means = []
    for _ in range(samples):
        starts = rng.integers(0, len(values), size=int(np.ceil(len(values) / block)))
        draw = np.concatenate([values[(np.arange(block) + start) % len(values)] for start in starts])[:len(values)]
        means.append(float(draw.mean()))
    low, high = np.quantile(means, [.025, .975])
    return {"status": "estimated", "expectancy_ci95": [float(low), float(high)],
            "samples": samples, "seed": seed, "block_length": block,
            "note": "conditional on this selected strategy and historical sample; not a future-return guarantee"}


def factory(name):
    if name == "smc":
        return StrategyEngine(MLPredictor(enabled=False))
    kind, period = name.split("-")
    return BaselineStrategy(kind, int(period))


def chronological_windows(start, end, folds):
    if folds < 1 or folds > 10 or end <= start:
        raise ValueError("folds must be 1..10 and end after start")
    span = end - start
    development_end = start + span * .8
    boundaries = [start + span * (.4 + .4 * i / folds) for i in range(folds + 1)]
    # Snap to the entry grid so requested windows remain executable.
    boundaries = [pd.Timestamp(value).floor("5min") for value in boundaries]
    development_end = pd.Timestamp(development_end).floor("5min")
    windows = [(pd.Timestamp(start), boundaries[i], boundaries[i + 1]) for i in range(folds)]
    return windows, development_end


def run_research(frames, symbol, info, start, end, output, calendar="fx", folds=3):
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
    root = Path(output) / run_id
    root.mkdir(parents=True, exist_ok=False)
    quality = inspect_dataset(frames, start, end, calendar, True)
    (root / "data_quality.json").write_text(json.dumps(quality, indent=2), encoding="utf-8")
    if not quality["ok"]:
        raise DataQualityError(f"research blocked by data quality; see {root / 'data_quality.json'}")
    snapshots = root / "data"
    snapshots.mkdir()
    for tf, frame in frames.items():
        frame.to_csv(snapshots / f"{tf}.csv", index_label="timestamp")
    (snapshots / "symbol.json").write_text(json.dumps(asdict(info), indent=2), encoding="utf-8")
    source = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for folder in ("backtest", "brokers", "core", "data", "execution", "ml", "risk", "strategy"):
        for path in sorted((source / folder).glob("*.py")):
            digest.update(str(path.relative_to(source)).encode())
            digest.update(path.read_bytes())
    digest.update((source / "config.py").read_bytes())
    digest.update((source / "main.py").read_bytes())
    manifest = {"run_id": run_id, "symbol": symbol, "start": str(start), "end": str(end),
        "python": platform.python_version(), "source_sha256": digest.hexdigest(),
        "packages": {name: importlib.metadata.version(name) for name in ("pandas", "numpy", "scikit-learn")},
        "data_digests": {tf: q["sha256"] for tf, q in quality["timeframes"].items()},
        "seed": 42, "selection_metric": "training net return after costs; require at least one trade",
        "candidates": ["smc", "trend-20", "trend-30", "breakout-20", "breakout-30"],
        "risk_per_trade": config.RISK_PER_TRADE, "max_symbol_exposure": config.SETTINGS.max_symbol_exposure_pct,
        "limitations": ["fixed-cost OHLC simulation", "fixed tick-value currency conversion", "approximate FX calendar",
            "no proof of profitability", "parameter search is logged; do not retune on final holdout"]}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    attempts: list[dict[str, Any]] = []

    def evaluate(name, begin, stop, phase, cost=1):
        path = root / f"{len(attempts):03d}-{phase}-{name}-cost{cost}"
        engine = BacktestEngine(symbol, symbol_info=info, strategy=factory(name), artifact_dir=path, cost_multiplier=cost,
                                capabilities=BrokerCapabilities(long=info.can_long, short=info.can_short))
        result = engine.run(data=frames, start=begin, end=stop, strict_data=True, calendar=calendar)
        row = {"phase": phase, "candidate": name, "start": str(begin), "end": str(stop),
            "cost_multiplier": cost, "stats": result["stats"], "artifacts": str(path.relative_to(root))}
        attempts.append(row)
        with (root / "experiments.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
        return result

    def select(begin, stop, phase):
        results = [(name, evaluate(name, begin, stop, phase)) for name in manifest["candidates"]]
        eligible = [(name, result) for name, result in results if result["stats"]["total_trades"] > 0]
        # No-trade and losing results never constitute an approved edge.
        if not eligible:
            return None
        name, result = max(eligible, key=lambda item: item[1]["stats"]["net_return"])
        return name if result["stats"]["net_return"] > 0 else None

    windows, development_end = chronological_windows(pd.Timestamp(start), pd.Timestamp(end), folds)
    rolling = []
    for index, (train_begin, train_end, test_end) in enumerate(windows):
        chosen = select(train_begin, train_end, f"fold{index}-train")
        result = evaluate(chosen, train_end, test_end, f"fold{index}-forward") if chosen else None
        rolling.append({"train_end": str(train_end), "test_end": str(test_end),
                        "selected": chosen, "forward_stats": result["stats"] if result else None})
    final_choice = select(pd.Timestamp(start), development_end, "development-selection")
    registry = Path(output) / "holdout_registry.jsonl"
    fingerprint = hashlib.sha256(json.dumps(manifest["data_digests"], sort_keys=True).encode()).hexdigest()
    previous = [json.loads(line) for line in registry.read_text(encoding="utf-8").splitlines()] if registry.exists() else []
    reused = any(row["data_fingerprint"] == fingerprint for row in previous)
    if final_choice:
        with registry.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"run_id": run_id, "data_fingerprint": fingerprint,
                "holdout_start": str(development_end), "end": str(end)}) + "\n")
    final = evaluate(final_choice, development_end, pd.Timestamp(end), "final-holdout") if final_choice else None
    stress = []
    if final_choice:
        for multiplier in (1.5, 2.0):
            result = evaluate(final_choice, development_end, pd.Timestamp(end), "holdout-cost-stress", multiplier)
            stress.append({"cost_multiplier": multiplier, "stats": result["stats"]})
    summary = {"run_id": run_id, "output": str(root), "rolling_folds": rolling,
        "final_selection": final_choice, "final_holdout_start": str(development_end),
        "final_holdout": final["stats"] if final else None, "cost_stress": stress,
        "uncertainty": block_bootstrap([t["pnl"] for t in final["trades"]]) if final else {"status": "no_candidate"},
        "experiment_count": len(attempts), "status": "research_only_no_live_approval",
        "holdout_previously_evaluated": reused,
        "decision": "collect independent demo evidence" if final and final["stats"]["net_return"] > 0 else "no_demonstrated_edge",
        "demo_requirement": "freeze selected configuration; compare fresh quote decisions and realized demo fills against assumptions"}
    (root / "research_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
