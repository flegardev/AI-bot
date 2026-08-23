import json

import pandas as pd

from backtest.engine import BacktestEngine


def candles(index):
    return pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0},
        index=index,
    )


def test_backtest_writes_all_required_artifacts_without_fake_trades(tmp_path):
    data = {
        "1h": candles(pd.date_range("2026-01-01", periods=10, freq="1h", tz="UTC")),
        "15m": candles(pd.date_range("2026-01-01", periods=20, freq="15min", tz="UTC")),
        "5m": candles(pd.date_range("2026-01-01", periods=40, freq="5min", tz="UTC")),
    }
    engine = BacktestEngine("EURUSD", artifact_dir=tmp_path)
    result = engine.run(data=data)
    expected = {
        "backtest_summary.json", "trades.csv", "equity_curve.csv",
        "drawdown_curve.csv", "monthly_returns.csv", "backtest_config_snapshot.json",
    }
    assert expected == {path.name for path in tmp_path.iterdir()}
    assert result["trades"] == []
    summary = json.loads((tmp_path / "backtest_summary.json").read_text())
    assert summary["total_trades"] == 0
    assert summary["assumptions"]["simultaneous_sl_tp"] == "stop_first"
