import pandas as pd

from ml.predictor import FEATURE_COLS, MLPredictor, chronological_split


def records(count=30):
    output = []
    for index in range(count):
        row = {column: float(index % 5) for column in FEATURE_COLS}
        row.update({
            "timestamp": pd.Timestamp("2020-01-01", tz="UTC") + pd.Timedelta(days=index),
            "result": "win" if index % 2 else "loss",
            "pnl": 1 if index % 2 else -1,
        })
        output.append(row)
    return output


def test_chronological_split_never_shuffles():
    train, validation, test = chronological_split(pd.DataFrame(list(reversed(records()))))
    assert train["timestamp"].max() < validation["timestamp"].min()
    assert validation["timestamp"].max() < test["timestamp"].min()


def test_walk_forward_trains_only_on_past(tmp_path):
    predictor = MLPredictor(str(tmp_path / "model.pkl"), enabled=True)
    result = predictor.walk_forward(records(40), min_train_samples=20, step=5)
    assert not result.empty
    assert (pd.to_datetime(result["trained_through"]) < pd.to_datetime(result["timestamp"])).all()


def test_disabled_ml_accepts_baseline_without_model(tmp_path):
    predictor = MLPredictor(str(tmp_path / "missing.pkl"), enabled=False)
    accepted, probability = predictor.is_confident({})
    assert accepted
    assert probability == 0.5
