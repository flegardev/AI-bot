"""Chronological, schema-checked ML trade filter. Disabled by default."""
from __future__ import annotations

import os
from typing import Any, cast

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import precision_score, recall_score, roc_auc_score
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

import config
import logger
from ml.features import FEATURE_COLS

MODEL_VERSION = "4.0-entry-v3-calibrated"


def chronological_split(
    frame: pd.DataFrame, train_fraction: float = 0.6, validation_fraction: float = 0.2
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if not 0 < train_fraction < 1 or not 0 <= validation_fraction < 1:
        raise ValueError("invalid split fractions")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("train + validation fractions must be below one")
    ordered = frame.copy()
    ordered["timestamp"] = pd.to_datetime(ordered["timestamp"], utc=True)
    ordered = ordered.sort_values("timestamp", kind="stable").reset_index(drop=True)
    train_end = int(len(ordered) * train_fraction)
    validation_end = int(len(ordered) * (train_fraction + validation_fraction))
    train, validation, test = ordered.iloc[:train_end], ordered.iloc[train_end:validation_end], ordered.iloc[validation_end:]
    if not validation.empty:
        train = purge_overlaps(train, validation["timestamp"].min())
    if not test.empty:
        validation = purge_overlaps(validation, test["timestamp"].min())
    return train, validation, test


def purge_overlaps(frame, boundary, embargo=None):
    """A training label must be known before the next evaluation period."""
    embargo = pd.Timedelta(minutes=5) if embargo is None else embargo
    outcomes = pd.to_datetime(frame.get("exit_timestamp", frame["timestamp"]), utc=True)
    return frame.loc[outcomes + embargo < pd.Timestamp(boundary)].copy()


class MLPredictor:
    def __init__(self, model_path: str | None = None, enabled: bool | None = None):
        self.model_path = model_path or config.ML_MODEL_PATH
        self.enabled = config.USE_ML_FILTER if enabled is None else enabled
        self.model: Any = None
        self.scaler: StandardScaler | None = None
        self.is_trained = False
        self.metadata: dict[str, Any] = {}
        self._try_load()

    def _try_load(self) -> None:
        if not os.path.exists(self.model_path):
            return
        try:
            bundle = joblib.load(self.model_path)
            if bundle.get("model_version") != MODEL_VERSION:
                raise ValueError("model must be retrained with purged validation")
            schema = bundle.get("feature_schema")
            if schema != FEATURE_COLS:
                raise ValueError("saved feature schema differs from serving schema")
            self.model, self.scaler = bundle["model"], bundle["scaler"]
            self.metadata = bundle.get("metadata", {})
            self.is_trained = True
        except Exception as exc:
            self.model = self.scaler = None
            self.is_trained = False
            logger.warning(f"Could not load ML model from {self.model_path}: {exc}")

    def save(self, metadata: dict[str, Any] | None = None) -> None:
        parent = os.path.dirname(self.model_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        joblib.dump({
            "model": self.model, "scaler": self.scaler,
            "feature_schema": FEATURE_COLS,
            "metadata": metadata or self.metadata,
            "model_version": MODEL_VERSION,
        }, self.model_path)

    @staticmethod
    def _prepare(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        missing = [column for column in FEATURE_COLS + ["result"] if column not in frame]
        if missing:
            raise ValueError(f"Missing ML columns: {missing}")
        values = frame[FEATURE_COLS].astype(float).to_numpy()
        if not np.isfinite(values).all():
            raise ValueError("ML features must be finite")
        return values, (frame["result"] == "win").astype(int).to_numpy()

    def train(self, records: list[dict[str, Any]], save: bool = True, *, split_times=None) -> dict[str, Any]:
        from pathlib import Path

        from ml.tournament import train_tournament
        output = Path(self.model_path).parent / (Path(self.model_path).stem + "-research") if save else None
        card, selected = train_tournament(records, output, split_times=split_times)
        self.metadata = card
        self.enabled = selected is not None
        self.is_trained = selected is not None
        if selected is not None:
            self.model, self.scaler = selected.model, selected.scaler
            if save:
                self.save(card)
        return card

    def walk_forward(
        self, records: list[dict[str, Any]], min_train_samples: int | None = None, step: int = 50
    ) -> pd.DataFrame:
        frame = pd.DataFrame(records)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        frame = frame.sort_values("timestamp").reset_index(drop=True)
        minimum = min_train_samples or config.ML_MIN_SAMPLES
        outputs = []
        for start in range(minimum, len(frame), step):
            test = frame.iloc[start:start + step]
            train = purge_overlaps(frame.iloc[:start], pd.to_datetime(test["timestamp"], utc=True).min())
            clone = MLPredictor(model_path=self.model_path + ".walk", enabled=True)
            clone.model = clone.scaler = None
            clone.is_trained = False
            x_train, y_train = clone._prepare(train)
            if len(np.unique(y_train)) < 2:
                continue
            clone.scaler = StandardScaler().fit(x_train)
            clone.model = RandomForestClassifier(
                n_estimators=100, max_depth=6, min_samples_leaf=10, random_state=42, n_jobs=1
            ).fit(clone.scaler.transform(x_train), y_train)
            clone.is_trained = True
            rows = cast(list[dict[str, Any]], test.to_dict("records"))
            probabilities = [clone.predict(row) for row in rows]
            for timestamp, probability in zip(test["timestamp"], probabilities, strict=True):
                outputs.append({"timestamp": timestamp, "probability": probability, "trained_through": train["timestamp"].iloc[-1]})
        return pd.DataFrame(outputs)

    def _evaluate_frame(self, frame: pd.DataFrame) -> dict[str, Any]:
        if frame.empty:
            return {}
        _, y_true = self._prepare(frame)
        rows = cast(list[dict[str, Any]], frame.to_dict("records"))
        probabilities = np.array([self._score(row) for row in rows])
        y_pred = probabilities >= config.ML_CONFIDENCE_THRESHOLD
        return {
            "samples": len(frame),
            "precision": float(precision_score(y_true, y_pred, zero_division=0)),
            "recall": float(recall_score(y_true, y_pred, zero_division=0)),
            "roc_auc": float(roc_auc_score(y_true, probabilities)) if len(np.unique(y_true)) > 1 else None,
            "calibration_mae": float(abs(probabilities.mean() - y_true.mean())),
            "brier_score": float(np.mean((probabilities - y_true) ** 2)),
            "accepted": int(y_pred.sum()),
            "rejected": int((~y_pred).sum()),
        }

    def _accepted_mask(self, frame: pd.DataFrame) -> np.ndarray:
        rows = cast(list[dict[str, Any]], frame.to_dict("records"))
        return np.array([self._score(row) >= config.ML_CONFIDENCE_THRESHOLD for row in rows])

    @staticmethod
    def _trading_metrics(frame: pd.DataFrame, mask: np.ndarray) -> dict[str, float]:
        selected = frame.loc[mask]
        pnl = selected.get("pnl", pd.Series(dtype=float)).astype(float)
        gross_profit = pnl[pnl > 0].sum()
        gross_loss = abs(pnl[pnl < 0].sum())
        equity = pd.concat([pd.Series([0.0]), pnl.cumsum()], ignore_index=True)
        drawdown = equity.cummax() - equity if not equity.empty else pd.Series([0.0])
        return {
            "accepted_trades": int(len(selected)),
            "net_return": float(pnl.sum()),
            "maximum_drawdown": float(drawdown.max()),
            "profit_factor": float(gross_profit / gross_loss) if gross_loss else 0.0,
        }

    def predict(self, features: dict[str, Any]) -> float:
        if not self.enabled or not self.is_trained or self.model is None or self.scaler is None:
            return 0.5
        return self._score(features)

    def _score(self, features: dict[str, Any]) -> float:
        if self.model is None or self.scaler is None:
            raise ValueError("model is not trained")
        missing = [column for column in FEATURE_COLS if column not in features]
        if missing:
            raise ValueError(f"Prediction missing feature(s): {missing}")
        values = np.array([[float(features[column]) for column in FEATURE_COLS]])
        if not np.isfinite(values).all():
            raise ValueError("prediction features must be finite")
        with threadpool_limits(limits=1):
            return float(self.model.predict_proba(self.scaler.transform(values))[0][1])

    def is_confident(self, features: dict[str, Any], threshold: float | None = None) -> tuple[bool, float]:
        if not self.enabled:
            return True, 0.5
        if not self.is_trained or not self.metadata.get("validation_approved"):
            return False, 0.5
        probability = self.predict(features)
        return probability >= (threshold if threshold is not None else self.metadata.get("threshold", config.ML_CONFIDENCE_THRESHOLD)), probability

    @staticmethod
    def extract_features(bias_details, entry_details, sweep_direction, df_5m, **kwargs):
        from ml.features import build_features
        return build_features(bias_details, entry_details, sweep_direction, df_5m, **kwargs).to_dict()


_predictor: MLPredictor | None = None


def get_predictor() -> MLPredictor:
    global _predictor
    if _predictor is None:
        _predictor = MLPredictor()
    return _predictor
