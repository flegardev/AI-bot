from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml.features import FEATURE_COLS, FEATURE_VERSION, FeatureVector, build_features
from ml.predictor import MLPredictor
from ml.tournament import train_tournament


def labeled_fixture(count=300):
    rows = []
    for index in range(count):
        timestamp = pd.Timestamp("2020-01-01", tz="UTC") + pd.Timedelta(days=index)
        row = {key: float(index % 2) for key in FEATURE_COLS}
        row.update(timestamp=timestamp, exit_timestamp=timestamp + pd.Timedelta(hours=1),
                   pnl=2. if index % 2 else -1., matured=True, feature_timestamp=timestamp,
                   feature_schema_version=FEATURE_VERSION, dataset_kind="synthetic", result="win" if index % 2 else "loss")
        rows.append(row)
    return rows


def test_three_models_and_rule_reference_are_actually_evaluated(tmp_path):
    card, selected = train_tournament(labeled_fixture(), tmp_path)
    assert {row["name"] for row in card["candidate_results"]} == {"logistic_regression", "random_forest", "hist_gradient_boosting"}
    assert all(row["status"] == "completed" for row in card["candidate_results"])
    assert selected is not None
    assert card["selection_basis"] == "validation_only"
    assert pd.Timestamp(card["training_label_cutoff"]) < pd.Timestamp(card["calibration_start"])
    assert pd.Timestamp(card["calibration_end"]) < pd.Timestamp(card["selection_end"])
    assert (tmp_path / "model_card.json").exists()
    assert card["selected_estimator"] in {row["name"] for row in card["candidate_results"]}


def test_unmatured_samples_do_not_create_a_model(tmp_path):
    rows = labeled_fixture()
    for row in rows:
        row["matured"] = False
    card, selected = train_tournament(rows, tmp_path)
    assert card["status"] == "insufficient_samples"
    assert card["unmatured_excluded"] == 300
    assert selected is None
    assert not list(tmp_path.glob("*.pkl"))


def test_future_availability_or_old_schema_is_rejected(tmp_path):
    rows = labeled_fixture()
    rows[0]["feature_timestamp"] += pd.Timedelta(days=1)
    with pytest.raises(ValueError, match="availability"):
        train_tournament(rows)
    rows = labeled_fixture()
    rows[0]["feature_schema_version"] = "old"
    with pytest.raises(ValueError, match="schema"):
        train_tournament(rows)


def test_selected_model_round_trips_to_serving_schema(tmp_path):
    model = MLPredictor(str(tmp_path / "model.pkl"), enabled=False)
    card = model.train(labeled_fixture(), save=True)
    loaded = MLPredictor(str(tmp_path / "model.pkl"), enabled=True)
    assert loaded.is_trained
    assert loaded.metadata["feature_schema_version"] == FEATURE_VERSION
    assert loaded.predict(labeled_fixture()[1]) == pytest.approx(model.predict(labeled_fixture()[1]))
    assert Path(model.model_path).exists()
    assert card["validation_approved"]


def test_typed_feature_snapshot_excludes_future_candle_mutations():
    index = pd.date_range("2024-01-01", periods=30, freq="5min", tz="UTC")
    frame = pd.DataFrame({"open":100.,"high":101.,"low":99.,"close":100.,"volume":1.}, index=index)
    when = index[25]
    a = build_features({}, {}, "bullish", frame, as_of=when)
    frame.loc[frame.index >= when, ["open","high","low","close"]] = [200,201,199,200]
    b = build_features({}, {}, "bullish", frame, as_of=when)
    assert a == b
    assert a.sha256 == b.sha256
    assert len(a.values) == len(FEATURE_COLS)
    with pytest.raises(ValueError):
        replace(a, values=tuple([np.nan] * len(FEATURE_COLS)))


def test_feature_vector_rejects_wrong_schema():
    with pytest.raises(ValueError):
        FeatureVector("2024-01-01T00:00:00Z", (1.,))


def test_declared_cutoffs_prevent_appended_test_rows_changing_selection():
    rows = labeled_fixture()
    split = (rows[180]["timestamp"], rows[240]["timestamp"])
    a, _ = train_tournament(rows, split_times=split)
    extra = labeled_fixture(400)[300:]
    for row in extra:
        row["pnl"] = -1000.
    b, _ = train_tournament(rows + extra, split_times=split)
    assert a["selected_estimator"] == b["selected_estimator"]
    assert a["ml_validation"] == b["ml_validation"]
    assert a["split_basis"] == "declared_utc_boundaries"
