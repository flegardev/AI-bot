import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from backtest import competition as module
from core.models import SymbolInfo
from data.fixtures import diagnostic_fixture
from strategy.competition import NoTrade


def test_flat_benchmark_never_emits_orders():
    assert NoTrade().evaluate("TEST", {}, pd.Timestamp("2024-01-01", tz="UTC")) is None


def test_budget_is_rejected_before_any_experiment(tmp_path):
    with pytest.raises(ValueError, match="budget"):
        module.competition({}, "TEST", SymbolInfo("TEST", 1, 1, 1, 1, 100, 1),
                           pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-02", tz="UTC"),
                           tmp_path, max_experiments=1)


def test_final_losses_cannot_change_selected_candidate(tmp_path):
    frames, info, start, end = diagnostic_fixture(tmp_path / "data")
    class Replay:
        final_loss = 0
        def __init__(self, *args, **kwargs):
            self.name = kwargs["strategy"].name
            self.path = kwargs["artifact_dir"]
        def run(self, **kwargs):
            return {"stats": {"net_return": self.final_loss if "-final_" in self.path.name
                              else .1 if self.name == "trend_momentum_v1" else -.1, "total_trades": 40},
                    "trades": [{"pnl": self.final_loss}] * 40}
    with patch.object(module, "BacktestEngine", Replay):
        a = module.competition(frames, info.symbol, info, start, end, tmp_path / "a", "24/7")
        Replay.final_loss = -1000
        b = module.competition(frames, info.symbol, info, start, end, tmp_path / "b", "24/7")
    assert a["selected"] == b["selected"]
    assert a["selected"]["name"] == "trend_momentum_v1"
    assert b["final_evaluation"]["net_return"] == -1000
    selection = json.loads((Path(b["output"]) / "selection.json").read_text())
    assert selection["selected"] == b["selected"]


def test_real_competition_on_synthetic_flat_data_has_no_winner(tmp_path):
    frames, info, start, end = diagnostic_fixture(tmp_path / "data")
    result = module.competition(frames, info.symbol, info, start, end, tmp_path / "results", "24/7")
    assert result["selected"] is None
    assert result["status"] == "rejected_no_validation_edge"
    assert result["experiments"] <= result["budget"]
    ledger = (Path(result["output"]) / "experiments.jsonl").read_text()
    assert '"no_trade"' in ledger and '"smc_baseline_v1"' in ledger
    assert all(json.loads(line)["status"] == "completed" for line in ledger.splitlines())
