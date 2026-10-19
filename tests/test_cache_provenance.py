import json
from unittest.mock import patch

import pandas as pd
import pytest

import main
from data.contracts import MarketDataContract
from data.fixtures import diagnostic_fixture


def test_normalized_cache_contract_cannot_apply_timezone_or_bid_conversion_twice():
    raw = pd.DataFrame({"open": 100., "high": 101., "low": 99., "close": 100., "volume": 1.},
                       index=pd.date_range("2024-07-01 12:00", periods=3, freq="5min"))
    source = MarketDataContract("synthetic bid fixture", "synthetic", "broker_wall_time", "Europe/Helsinki", "bid")
    converted = source.normalize(raw, .2)
    stored = MarketDataContract(**converted.attrs["data_contract"])
    reloaded = stored.normalize(converted, .2)
    assert reloaded["close"].equals(converted["close"])
    assert reloaded.index.equals(converted.index)
    assert converted["close"].iloc[0] == pytest.approx(100.1)
    assert converted.index[0].hour == 9


def test_recaching_synthetic_data_never_relabels_it_market(tmp_path):
    _, info, start, end = diagnostic_fixture(tmp_path / "input")
    command = ["main.py", "cache-data", "--data-dir", str(tmp_path / "input"), "--symbol", info.symbol,
               "--calendar", "24/7", "--start", str(start), "--end", str(end), "--output", str(tmp_path / "copy")]
    with patch("sys.argv", command):
        assert main.main() == 0
    contract = json.loads((tmp_path / "copy" / "contract.json").read_text())
    assert contract["dataset_kind"] == "synthetic"
    assert contract["source"] == "constant-price software fixture"
    quality = json.loads((tmp_path / "copy" / "data_quality.json").read_text())
    assert quality["timeframes"]["5m"]["data_contract"] == contract


def test_legacy_offline_cache_never_silently_assumes_naive_times_are_utc(tmp_path):
    for tf in ("1h", "15m", "5m"):
        pd.DataFrame({"timestamp": ["2024-01-01 12:00:00"], "open": [1], "high": [1], "low": [1], "close": [1], "volume": [1]}).to_csv(tmp_path / f"{tf}.csv", index=False)
    command = ["main.py", "backtest", "--data-dir", str(tmp_path), "--output", str(tmp_path / "blocked")]
    with patch("sys.argv", command):
        assert main.main() == 2
    assert "timezone-aware" in json.loads((tmp_path / "blocked" / "blocked.json").read_text())["error"]
