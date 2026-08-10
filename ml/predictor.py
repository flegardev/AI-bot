"""
ml/predictor.py - ML-based trade confidence filter.
"""
import os
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

import config
import logger

FEATURE_COLS = [
    "ema_distance_pct",
    "rsi_14",
    "volume_ratio",
    "atr_pct",
    "sweep_direction",
    "signal_type",
]


class MLPredictor:
    """Wraps a RandomForest classifier with a scaler."""

    def __init__(self, model_path: str | None = None):
        self.model_path = model_path or config.ML_MODEL_PATH
        self.model: RandomForestClassifier | None = None
        self.scaler: StandardScaler | None = None
        self.is_trained = False
        self._try_load()

    def _try_load(self):
        """Load a trusted local model bundle if it exists.

        joblib uses pickle semantics and can execute code while loading. The
        configured artifact must be locally trained or otherwise trusted and
        integrity-controlled; error handling here is not a security sandbox.
        """
        if os.path.exists(self.model_path):
            try:
                bundle = joblib.load(self.model_path)
                self.model = bundle["model"]
                self.scaler = bundle["scaler"]
                self.is_trained = True
                logger.info(f"ML model loaded from {self.model_path}")
            except Exception as e:
                self.model = None
                self.scaler = None
                self.is_trained = False
                logger.warning(f"Could not load ML model from {self.model_path}: {e}")

    def save(self, metadata: dict | None = None):
        """Save the current model bundle."""
        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
        joblib.dump({"model": self.model, "scaler": self.scaler, "metadata": metadata or {}}, self.model_path)
        logger.success(f"ML model saved to {self.model_path}")

    def train(self, records: list[dict]) -> dict:
        """Train the model from backtest trade records."""
        if len(records) < config.ML_MIN_SAMPLES:
            logger.warning(
                f"Only {len(records)} samples - need {config.ML_MIN_SAMPLES} to train ML model."
            )
            return {}

        df = pd.DataFrame(records)
        missing = [c for c in FEATURE_COLS if c not in df.columns]
        if missing:
            logger.warning(f"Missing ML features: {missing} - skipping training.")
            return {}

        X = df[FEATURE_COLS].values.astype(float)
        y = (df["result"] == "win").astype(int).values
        if len(set(y)) < 2:
            logger.warning("ML training skipped - samples contain only one class.")
            return {}
        if min(np.bincount(y)) < 2:
            logger.warning("ML training skipped - each class needs at least two samples.")
            return {}

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )

        self.scaler = StandardScaler()
        X_train = self.scaler.fit_transform(X_train)
        X_test = self.scaler.transform(X_test)

        self.model = RandomForestClassifier(
            n_estimators=100,
            max_depth=6,
            min_samples_leaf=3,
            random_state=42,
            class_weight="balanced",
        )
        self.model.fit(X_train, y_train)
        self.is_trained = True

        y_pred = self.model.predict(X_test)
        acc = accuracy_score(y_test, y_pred)
        report = classification_report(y_test, y_pred, target_names=["loss", "win"], zero_division=0)

        logger.success(f"ML model trained - accuracy: {acc:.1%}")
        logger.info(f"\n{report}")

        self.save({"trained_at": datetime.now(timezone.utc).isoformat(), "samples": len(records)})
        return {"accuracy": acc, "report": report}

    def predict(self, features: dict) -> float:
        """Return win probability for a proposed trade."""
        if not self.is_trained or self.model is None or self.scaler is None:
            logger.warning("ML model not trained yet - returning 0.5 confidence.")
            return 0.5

        try:
            x = np.array([[features.get(col, 0.0) for col in FEATURE_COLS]], dtype=float)
            x_scaled = self.scaler.transform(x)
            prob = self.model.predict_proba(x_scaled)[0][1]
            return float(prob)
        except Exception as e:
            logger.error(f"ML prediction error: {e}")
            return 0.5

    def is_confident(
        self, features: dict, threshold: float | None = None
    ) -> tuple[bool, float]:
        """Return (take_trade, confidence)."""
        thr = config.ML_CONFIDENCE_THRESHOLD if threshold is None else threshold
        conf = self.predict(features)
        return conf >= thr, conf

    @staticmethod
    def extract_features(
        bias_details: dict,
        entry_details: dict,
        sweep_direction: str,
        df_5m: "pd.DataFrame",
    ) -> dict:
        """Build the feature dict expected by predict()."""
        vol_avg = df_5m["volume"].rolling(20).mean().iloc[-1]
        vol_ratio = (df_5m["volume"].iloc[-1] / vol_avg) if pd.notna(vol_avg) and vol_avg > 0 else 1.0

        return {
            "ema_distance_pct": bias_details.get("ema_distance_pct", 0.0),
            "rsi_14": entry_details.get("rsi_14", 50.0),
            "volume_ratio": vol_ratio,
            "atr_pct": entry_details.get("atr_pct", 0.0),
            "sweep_direction": 1 if sweep_direction == "bullish" else -1,
            "signal_type": 1 if entry_details.get("type") == "CHoCH" else 0,
        }


_predictor: MLPredictor | None = None


def get_predictor() -> MLPredictor:
    global _predictor
    if _predictor is None:
        _predictor = MLPredictor()
    return _predictor
