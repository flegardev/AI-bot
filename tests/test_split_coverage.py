import pandas as pd

from data.fixtures import diagnostic_fixture
from data.quality import inspect_dataset, inspect_frame


def test_validation_window_crossing_hourly_boundaries_has_valid_closed_context(tmp_path):
    frames, _, start, _ = diagnostic_fixture(tmp_path)
    report = inspect_dataset(frames, start + pd.Timedelta(hours=3, minutes=35),
        start + pd.Timedelta(hours=4, minutes=45), "24/7", True)
    assert report["ok"]
    assert report["timeframes"]["1h"]["expected_rows"] == 1
    assert report["timeframes"]["1h"]["evaluation_rows"] == 1


def test_subhour_higher_timeframe_window_can_use_its_prior_warmup(tmp_path):
    frames, _, start, _ = diagnostic_fixture(tmp_path)
    begin = start + pd.Timedelta(hours=3, minutes=5)
    report = inspect_dataset(frames, begin, begin + pd.Timedelta(minutes=25), "24/7", True)
    assert report["ok"] and report["timeframes"]["1h"]["expected_rows"] == 0
    assert report["timeframes"]["1h"]["warmup_rows"] >= 200


def test_candle_closing_inside_partial_window_cannot_be_silently_missing(tmp_path):
    frames, _, start, _ = diagnostic_fixture(tmp_path)
    missing = frames["1h"].drop(start + pd.Timedelta(hours=3))
    report = inspect_frame(missing, "1h", start + pd.Timedelta(hours=3, minutes=35),
        start + pd.Timedelta(hours=4, minutes=45), "24/7", 200)
    assert not report["ok"] and report["missing_bars"] == 1
