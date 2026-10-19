"""Calibrated small-model competition. No candidate sees test outcomes during selection."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import sklearn
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.frozen import FrozenEstimator
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

import config
from ml.features import FEATURE_COLS, FEATURE_VERSION


def train_tournament(records, output=None, *, as_of=None, split_times=None):
    from ml.predictor import MODEL_VERSION, MLPredictor, chronological_split, purge_overlaps
    frame = pd.DataFrame(records)
    if "timestamp" not in frame:
        raise ValueError("Chronological ML training requires a timestamp")
    required = {"exit_timestamp", "pnl"}
    if required.difference(frame.columns):
        raise ValueError("ML training requires outcome timestamps and net pnl")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame["exit_timestamp"] = pd.to_datetime(frame["exit_timestamp"], utc=True)
    if frame["timestamp"].isna().any() or frame["exit_timestamp"].isna().any() or (frame["exit_timestamp"] < frame["timestamp"]).any():
        raise ValueError("invalid ML outcome intervals")
    if frame["timestamp"].duplicated().any():
        raise ValueError("duplicate ML decision times require an explicit grouped-instrument protocol")
    observed = pd.Timestamp(as_of or datetime.now(timezone.utc))
    mature = frame["exit_timestamp"] <= observed
    if "matured" in frame:
        mature &= frame["matured"].isin([True, "True", "true", 1, "1"])
    excluded = int((~mature).sum())
    frame = frame.loc[mature].copy()
    if "feature_timestamp" in frame and (pd.to_datetime(frame["feature_timestamp"], utc=True) > frame["timestamp"]).any():
        raise ValueError("feature availability is after label start")
    if "feature_schema_version" in frame and not frame["feature_schema_version"].eq(FEATURE_VERSION).all():
        raise ValueError("training feature schema requires recollection")
    if len(frame) < config.ML_MIN_SAMPLES:
        return {"status": "insufficient_samples", "samples": len(frame), "required": config.ML_MIN_SAMPLES,
                "unmatured_excluded": excluded}, None
    if not np.isfinite(frame["pnl"].astype(float)).all():
        raise ValueError("nonfinite net labels")
    frame["result"] = np.where(frame["pnl"].astype(float) > 0, "win", "loss")
    if split_times is None:
        train, validation, test = chronological_split(frame)
    else:
        train_end, selection_end = map(pd.Timestamp, split_times)
        if train_end >= selection_end:
            raise ValueError("invalid declared ML split boundaries")
        ordered = frame.sort_values("timestamp")
        train = purge_overlaps(ordered.loc[ordered["timestamp"] < train_end], train_end)
        validation = purge_overlaps(ordered.loc[(ordered["timestamp"] >= train_end) & (ordered["timestamp"] < selection_end)], selection_end)
        test = ordered.loc[ordered["timestamp"] >= selection_end]
    if min(len(train), len(validation), len(test)) < 10:
        return {"status": "insufficient_purged_samples"}, None
    calibration_start = int(len(train) * .8)
    calibration = train.iloc[calibration_start:]
    fit = purge_overlaps(train.iloc[:calibration_start], calibration["timestamp"].min())
    if min(len(fit), len(calibration)) < 10:
        return {"status": "insufficient_calibration_samples"}, None
    x_fit, y_fit = MLPredictor._prepare(fit)
    x_cal, y_cal = MLPredictor._prepare(calibration)
    if min(len(np.unique(y_fit)), len(np.unique(y_cal))) < 2:
        return {"status": "single_class_training"}, None
    scaler = StandardScaler().fit(x_fit)
    candidates = {
        "logistic_regression": LogisticRegression(C=1.0, max_iter=1000, random_state=42),
        "random_forest": RandomForestClassifier(n_estimators=100, max_depth=4, min_samples_leaf=10, random_state=42, n_jobs=1),
        "hist_gradient_boosting": HistGradientBoostingClassifier(max_iter=100, max_leaf_nodes=7,
            learning_rate=.05, random_state=42, early_stopping=False),
    }
    baseline = MLPredictor._trading_metrics(validation, np.ones(len(validation), dtype=bool))
    rows: list[dict[str, Any]] = []
    fitted: dict[str, MLPredictor] = {}
    for name, estimator in candidates.items():
        try:
            with threadpool_limits(limits=1):
                estimator.fit(scaler.transform(x_fit), y_fit)
                calibrated = CalibratedClassifierCV(FrozenEstimator(estimator), method="sigmoid")
                calibrated.fit(scaler.transform(x_cal), y_cal)
            predictor = MLPredictor(model_path=":unfitted-tournament:", enabled=True)
            predictor.model, predictor.scaler, predictor.is_trained = calibrated, scaler, True
            metrics = predictor._trading_metrics(validation, predictor._accepted_mask(validation))
            eligible = metrics["net_return"] > max(0.0, baseline["net_return"]) and metrics["maximum_drawdown"] <= baseline["maximum_drawdown"] and metrics["accepted_trades"] >= 10
            rows.append({"name": name, "status": "completed", "validation_eligible": eligible,
                         "validation_trading": metrics, "validation_scores": predictor._evaluate_frame(validation)})
            fitted[name] = predictor
        except (ValueError, RuntimeError) as exc:
            rows.append({"name": name, "status": "failed", "error": str(exc), "validation_eligible": False})
    eligible_candidates = [row for row in rows if row["validation_eligible"]]
    winner = max(eligible_candidates, key=lambda row: row["validation_trading"]["net_return"]) if eligible_candidates else None
    # Even a rejected tournament reports validation candidates, without writing a junk model.
    chosen = fitted[winner["name"]] if winner else None
    dataset_hash = hashlib.sha256(pd.util.hash_pandas_object(frame[FEATURE_COLS + ["timestamp", "exit_timestamp", "pnl"]], index=False).to_numpy().tobytes()).hexdigest()
    card = {"status": "trained" if chosen else "rejected_no_validation_edge", "samples": len(frame),
        "feature_schema": FEATURE_COLS, "feature_schema_version": FEATURE_VERSION, "model_version": MODEL_VERSION,
        "estimator_version": sklearn.__version__, "dataset_sha256": dataset_hash, "unmatured_excluded": excluded,
        "train_end": str(fit["timestamp"].max()), "training_label_cutoff": str(fit["exit_timestamp"].max()),
        "calibration_start": str(calibration["timestamp"].min()), "calibration_end": str(calibration["exit_timestamp"].max()),
        "selection_end": str(validation["exit_timestamp"].max()), "test_start": str(test["timestamp"].min()),
        "selection_basis": "validation_only", "selected_estimator": winner["name"] if winner else None,
        "split_basis": "declared_utc_boundaries" if split_times else "predeclared_60_20_20_chronological_rows",
        "declared_test_start": str(split_times[1]) if split_times else None,
        "validation_approved": chosen is not None, "threshold": config.ML_CONFIDENCE_THRESHOLD,
        "baseline_validation": baseline, "ml_validation": winner["validation_trading"] if winner else None,
        "candidate_results": rows, "random_seed": 42,
        "label_definition": "positive net P&L; forced end-of-data closures and unresolved outcomes excluded",
        "hyperparameters": {name: estimator.get_params(deep=False) for name, estimator in candidates.items()},
        "feature_reference": {"mean": dict(zip(FEATURE_COLS, x_fit.mean(axis=0).tolist(), strict=True)),
                              "std": dict(zip(FEATURE_COLS, x_fit.std(axis=0).tolist(), strict=True))},
        "limitation": "trade-table filtering comparison; independent portfolio replay remains required"}
    card["dataset_kind"] = ("synthetic" if "dataset_kind" in frame and frame["dataset_kind"].eq("synthetic").all()
                            else "market" if "dataset_kind" in frame and frame["dataset_kind"].eq("market").all() else "unknown")
    if output:
        root = Path(output)
        root.mkdir(parents=True, exist_ok=True)
        (root / "selection.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    # Test is accessed only after the estimator and every eligibility decision are frozen.
    card["baseline_test"] = MLPredictor._trading_metrics(test, np.ones(len(test), dtype=bool))
    card["ml_test"] = chosen._trading_metrics(test, chosen._accepted_mask(test)) if chosen else None
    card["untouched_test"] = chosen._evaluate_frame(test) if chosen else None
    if chosen:
        chosen.metadata = card
    if output:
        (root / "model_card.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
        (root / "model_card.md").write_text("# ML model card\n\n" + json.dumps(card, indent=2) + "\n\nNo prospective profitability conclusion.\n", encoding="utf-8")
    return card, chosen
