"""Small past-only model robustness folds entirely inside the training partition."""
import numpy as np
from sklearn.base import clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits


def robustness_folds(training, estimators):
    from ml.predictor import MLPredictor, purge_overlaps
    rows = []
    for fraction, stop_fraction in ((.65, .8), (.8, 1.0)):
        evaluation = training.iloc[int(len(training) * fraction):int(len(training) * stop_fraction)]
        if evaluation.empty:
            continue
        past = purge_overlaps(training.iloc[:int(len(training) * fraction)], evaluation["timestamp"].min())
        calibration = past.iloc[int(len(past) * .8):]
        fit = purge_overlaps(past.iloc[:int(len(past) * .8)], calibration["timestamp"].min()) if not calibration.empty else past.iloc[:0]
        for name, prototype in estimators.items():
            row = {"name": name, "evaluation_start": str(evaluation["timestamp"].min()),
                   "evaluation_end": str(evaluation["exit_timestamp"].max()), "selection_use": "diagnostic_only"}
            try:
                if min(len(fit), len(calibration), len(evaluation)) < 10:
                    raise ValueError("insufficient purged fold/calibration samples")
                x_fit, y_fit = MLPredictor._prepare(fit)
                x_cal, y_cal = MLPredictor._prepare(calibration)
                if min(len(np.unique(y_fit)), len(np.unique(y_cal))) < 2:
                    raise ValueError("single-class fold/calibration labels")
                scaler = StandardScaler().fit(x_fit)
                with threadpool_limits(limits=1):
                    estimator = clone(prototype).fit(scaler.transform(x_fit), y_fit)
                    calibrated = CalibratedClassifierCV(FrozenEstimator(estimator), method="sigmoid").fit(scaler.transform(x_cal), y_cal)
                predictor = MLPredictor(":walkforward-ephemeral:", enabled=True)
                predictor.model, predictor.scaler, predictor.is_trained = calibrated, scaler, True
                row.update(status="completed", fit_label_cutoff=str(fit["exit_timestamp"].max()),
                    calibration_start=str(calibration["timestamp"].min()), calibration_label_cutoff=str(calibration["exit_timestamp"].max()),
                    scores=predictor._evaluate_frame(evaluation), trading=predictor._trading_metrics(evaluation, predictor._accepted_mask(evaluation)),
                    rule_only=predictor._trading_metrics(evaluation, np.ones(len(evaluation), dtype=bool)))
            except (ValueError, RuntimeError) as exc:
                row.update(status="failed", error=str(exc))
            rows.append(row)
    return rows
