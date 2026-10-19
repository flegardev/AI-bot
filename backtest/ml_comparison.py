"""Evaluate a selected ML filter and simple references on one final portfolio window."""
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd

from backtest.engine import BacktestEngine
from core.strategy_engine import StrategyEngine
from ml.predictor import MLPredictor
from strategy.competition import candidate, original_bias


def compare_ml(frames, symbol, info, start, end, output, calendar="fx_ny"):
    root = Path(output) / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8])
    root.mkdir(parents=True, exist_ok=False)
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    train_end = (start + (end - start) * .6).floor("5min")
    test_start = (start + (end - start) * .8).floor("5min")
    attempts = []
    def run(name, strategy, begin, stop):
        try:
            result = BacktestEngine(symbol, symbol_info=info, strategy=strategy, artifact_dir=root / name).run(
                data=frames, start=begin, end=stop, strict_data=True, calendar=calendar)
            record = {"name": name, "status": "completed", "start": str(begin), "end": str(stop), "stats": result["stats"]}
        except (ValueError, RuntimeError) as exc:
            record = {"name": name, "status": "blocked", "error": str(exc)}
            result = None
        attempts.append(record)
        with (root / "experiments.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        return result
    collected = run("unfiltered_label_collection", candidate("smc_baseline_v1"), start, end)
    predictor = MLPredictor(str(root / "selected_model.pkl"), enabled=False)
    if collected and collected["trades"]:
        card = predictor.train(collected["trades"], split_times=(train_end, test_start))
    else:
        card = {"status": "insufficient_samples", "samples": 0}
    (root / "training_status.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    comparison = {}
    if collected:
        for name in ("no_trade", "smc_baseline_v1", "trend_momentum_v1", "atr_breakout_v1"):
            result = run("test_" + name, candidate(name), test_start, end)
            comparison[name] = result["stats"] if result else None
        if predictor.enabled:
            result = run("test_selected_ml", StrategyEngine(predictor, bias_provider=original_bias), test_start, end)
            comparison["selected_ml"] = result["stats"] if result else None
    report = {"output": str(root), "training": card, "final_comparison": comparison,
        "selection_period_end": str(test_start), "paper_only": True,
        "status": "unproven" if predictor.enabled else "ml_not_eligible", "attempts": len(attempts)}
    (root / "ml_comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
