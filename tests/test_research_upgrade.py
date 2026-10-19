"""Regression tests for executable prices, data failures, lifecycle and ML leakage."""
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

import config
import main
from backtest.demo import export_demo_report
from backtest.engine import BacktestEngine
from backtest.research import block_bootstrap, chronological_windows, run_research
from brokers.mt5 import MT5BrokerAdapter
from brokers.paper import PaperBrokerAdapter
from core.models import Direction, OrderRequest, OrderStatus, SymbolInfo, TradeSignal
from core.quotes import Quote
from core.strategy_engine import StrategyEngine
from data.quality import DataQualityError, inspect_frame
from ml.predictor import FEATURE_COLS, MLPredictor, chronological_split
from persistence.store import TradingStore
from tests.test_mt5_adapter import FakeMT5


def frame(index, price=100.0):
    return pd.DataFrame({"open": price, "high": price + .5, "low": price - .5,
                         "close": price, "volume": 1.0}, index=index)


def paper(tmp_path, **costs):
    store = TradingStore(tmp_path / "paper.db")
    broker = PaperBrokerAdapter(store, 1000, SymbolInfo("TEST", 1, 1, 1, 1, 100, 1, leverage=10), **costs)
    when = datetime(2024, 1, 1, tzinfo=timezone.utc)
    broker.set_clock(when)
    signal = TradeSignal("TEST", Direction.LONG, 100, 90, 120, when, "test")
    return broker, store, signal


def test_paper_uses_execution_reference_and_clock_not_old_signal(tmp_path):
    broker, _, signal = paper(tmp_path, spread=2, slippage_pct=.01)
    when = signal.timestamp + timedelta(minutes=5)
    result = broker.place_order(OrderRequest(signal, 1, "gap", reference_price=110, execution_timestamp=when))
    assert result.fill_price == pytest.approx(112.1)
    assert broker.get_open_positions()[0].opened_at == when


def test_gap_through_stop_rejects_without_creating_position(tmp_path):
    broker, _, signal = paper(tmp_path)
    result = broker.place_order(OrderRequest(signal, 1, "bad-gap", reference_price=85))
    assert result.status == OrderStatus.REJECTED
    assert not broker.get_open_positions()


def test_daily_loss_reset_survives_restart_and_entry_fee_counted(tmp_path):
    broker, store, signal = paper(tmp_path, commission_per_lot=1)
    broker.place_order(OrderRequest(signal, 1, "entry"))
    assert broker.balance == 999
    broker.process_bar("TEST", pd.Series({"open": 100, "high": 101, "low": 89, "close": 95}), signal.timestamp)
    assert broker.realized_today == -12
    restored = PaperBrokerAdapter(store, 1000, broker.get_symbol_info("TEST"))
    restored.set_clock(signal.timestamp + timedelta(days=1))
    assert restored.realized_today == 0
    assert restored.balance == 988


def test_midpoint_short_exit_uses_ask_and_swap_is_debited(tmp_path):
    broker, store, signal = paper(tmp_path, spread=2, swap_per_lot_day=3)
    signal = replace(signal, direction=Direction.SHORT, stop_loss=110, take_profit=80)
    broker.place_order(OrderRequest(signal, 1, "short"))
    # Mid high 109 + half-spread 1 touches the short stop at 110.
    broker.process_bar("TEST", pd.Series({"open": 100, "high": 109, "low": 99, "close": 100}), signal.timestamp + timedelta(days=1))
    event = store.recent_events(1, "position_closed")[0]["payload"]
    assert event["price"] == 110
    assert event["swap"] == 3
    assert broker.balance == 986  # short entered at 99, lost 11 plus swap 3


class Once:
    def __init__(self):
        self.calls = 0
        self.diagnostics = Counter()

    def evaluate(self, symbol, candles, as_of, **kwargs):
        self.calls += 1
        if self.calls != 1:
            return None
        return TradeSignal(symbol, Direction.LONG, 100, 95, 120, as_of.to_pydatetime(), "fixed", signal_id="fixed")


def test_backtest_executes_later_open_and_processes_same_bar_stop(tmp_path):
    index = pd.date_range("2024-01-01", periods=3, freq="5min", tz="UTC")
    data = frame(index)
    data.iloc[1] = [105, 106, 94, 100, 1]
    frames = {"5m": data, "15m": frame(pd.date_range(index[0], periods=2, freq="15min")),
              "1h": frame(pd.date_range(index[0], periods=2, freq="1h"))}
    with patch.object(config, "BACKTEST_SLIPPAGE_PCT", 0), patch.object(config, "BACKTEST_SPREAD", 0), patch.object(config, "BACKTEST_FEE_RATE", 0):
        result = BacktestEngine("TEST", symbol_info=SymbolInfo("TEST", 1, 1, 1, 1, 100, 1, leverage=10),
            strategy=Once(), artifact_dir=tmp_path).run(data=frames)
    trade = result["trades"][0]
    assert trade["entry_price"] == 105
    assert trade["entry_timestamp"] == index[1].isoformat()
    assert trade["exit_price"] == 95
    assert pd.Timestamp(trade["exit_timestamp"]) == index[1] + pd.Timedelta(minutes=5)
    assert trade["result"] == "loss"


@pytest.mark.parametrize("bad", ["missing", "nan", "duplicates", "negative"])
def test_quality_rejects_bad_data(bad):
    index = pd.date_range("2024-01-01", periods=12, freq="5min", tz="UTC")
    data = frame(index)
    if bad == "missing":
        data = data.drop(index[4])
    elif bad == "nan":
        data.iloc[4, 0] = np.nan
    elif bad == "duplicates":
        data = pd.concat([data, data.iloc[-1:]])
    else:
        data.iloc[4, 4] = -1
    report = inspect_frame(data, "5m", index[0], index[-1] + pd.Timedelta(minutes=5))
    assert not report["ok"]


def test_fx_weekend_is_not_reported_as_missing_bars():
    index = pd.DatetimeIndex(["2024-01-05 21:00Z", "2024-01-07 22:00Z"])
    report = inspect_frame(frame(index), "1h", index[0], index[-1] + pd.Timedelta(hours=1), "fx")
    assert report["ok"]


def test_strict_backtest_writes_quality_and_blocks_performance(tmp_path):
    index = pd.date_range("2024-01-01", periods=3, freq="5min", tz="UTC")
    frames = {tf: frame(index) for tf in ("1h", "15m", "5m")}
    with pytest.raises(DataQualityError):
        BacktestEngine(artifact_dir=tmp_path).run(data=frames, start=index[0], end=index[-1], strict_data=True)
    assert (tmp_path / "data_quality.json").exists()
    assert not (tmp_path / "backtest_summary.json").exists()


@pytest.mark.parametrize("bid,ask,age", [(1, 1.1, 1000), (1, 1.1, -100), (1.2, 1.1, 0), (np.nan, 1, 0), (1, 2, 0)])
def test_quote_failures_rejected(bid, ask, age):
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        Quote(bid, ask, now - timedelta(seconds=age)).validate(120, .2, now)


def test_mt5_stale_quote_never_reaches_order_check_or_send():
    fake = FakeMT5()
    tick = fake.symbol_info_tick("EURUSD")
    tick.time = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    fake.symbol_info_tick = lambda symbol: tick
    adapter = MT5BrokerAdapter(fake)
    signal = TradeSignal("EURUSD", Direction.LONG, 1.1, 1.09, 1.12, datetime.now(timezone.utc), "test")
    result = adapter.place_order(OrderRequest(signal, .1, "stale", max_slippage=.001))
    assert result.status == OrderStatus.REJECTED
    assert "stale" in result.message
    assert not fake.sent


def test_paper_replay_exits_once_across_repeated_cycles(tmp_path):
    broker, store, signal = paper(tmp_path)
    broker.place_order(OrderRequest(signal, 1, "entry"))
    index = pd.date_range(signal.timestamp, periods=2, freq="5min")
    data = frame(index)
    data.iloc[1, 2] = 89
    main.replay_paper_exits(broker, data, store, "TEST")
    main.replay_paper_exits(broker, data, store, "TEST")
    assert len(store.recent_events(10, "position_closed")) == 1
    assert not broker.get_open_positions()


def ml_records(n=240):
    result = []
    for i in range(n):
        row = {col: float(i % 2) for col in FEATURE_COLS}
        timestamp = pd.Timestamp("2020-01-01", tz="UTC") + pd.Timedelta(days=i)
        row.update(timestamp=timestamp, exit_timestamp=timestamp + pd.Timedelta(hours=1),
                   pnl=2.0 if i % 2 else -1.0, result="win" if i % 2 else "loss")
        result.append(row)
    return result


def test_overlapping_labels_are_purged_before_next_partition():
    data = pd.DataFrame(ml_records(30))
    data.loc[17, "exit_timestamp"] = data.loc[20, "timestamp"]
    train, validation, test = chronological_split(data)
    assert data.loc[17, "timestamp"] not in set(train["timestamp"])
    assert pd.to_datetime(train["exit_timestamp"], utc=True).max() < validation["timestamp"].min()
    assert pd.to_datetime(validation["exit_timestamp"], utc=True).max() < test["timestamp"].min()


def test_test_outcomes_cannot_change_ml_selection(tmp_path):
    first, second = ml_records(), ml_records()
    for row in second[192:]:
        row["pnl"] = -10000.0
    a = MLPredictor(str(tmp_path / "a.pkl"), enabled=False).train(first, save=False)
    b = MLPredictor(str(tmp_path / "b.pkl"), enabled=False).train(second, save=False)
    assert a["status"] == "trained"
    assert a["validation_approved"] == b["validation_approved"]
    assert a["baseline_validation"] == b["baseline_validation"]
    assert a["ml_validation"] == b["ml_validation"]
    assert a["ml_test"] != b["ml_test"]


def test_signal_features_are_numeric_and_ml_is_called():
    frames = {tf: frame(pd.date_range("2024-01-01", periods=240, freq=freq, tz="UTC"))
              for tf, freq in [("1h", "1h"), ("15m", "15min"), ("5m", "5min")]}
    class Predictor:
        enabled = True
        is_trained = True
        metadata = {"selection_end": "2023-01-01T00:00:00Z"}
        def is_confident(self, features):
            assert set(FEATURE_COLS).issubset(features)
            assert all(isinstance(features[col], (float, int)) for col in FEATURE_COLS)
            return False, .1
    engine = StrategyEngine(Predictor())
    with patch("core.strategy_engine.get_bias_details", return_value={"bias": "LONG"}), patch("core.strategy_engine.detect_liquidity_sweep", return_value={"swept": True, "direction": "bullish", "level": 95}), patch("core.strategy_engine.get_entry_details", return_value={"signal": True, "current_price": 100, "atr_14": 1, "type": "BOS"}):
        assert engine.evaluate("TEST", frames, pd.Timestamp("2024-02-01", tz="UTC")) is None
    assert engine.last_reason == "ml_rejected"


def test_research_final_holdout_follows_all_development_windows():
    windows, holdout = chronological_windows(pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-02-01", tz="UTC"), 3)
    for begin, train_end, test_end in windows:
        assert begin < train_end < test_end <= holdout
    assert windows[-1][2] == holdout
    assert block_bootstrap([1, -1])["status"] == "insufficient_trades"


def test_cli_spread_and_freshness_block_even_without_signal(tmp_path):
    class Market:
        connected = False
        def connect(self): self.connected = True
        def disconnect(self): self.connected = False
        def get_symbol_info(self, symbol): return SymbolInfo(symbol, 1, 1, 1, 1, 100, 1, leverage=10)
        def get_candles(self, symbol, timeframe):
            return frame(pd.date_range("2024-01-01", periods=2, freq="1h", tz="UTC"))
        def get_quote(self, symbol):
            return Quote(100, 100.1, datetime(2024, 1, 1, tzinfo=timezone.utc))
    market = Market()
    args = main.parser().parse_args(["paper", "--once"])
    with patch.object(config, "DATABASE_PATH", str(tmp_path / "db")), patch("main.make_broker", return_value=market):
        assert main.run_cycle(args, live=False) == 2
    assert not market.connected


def test_warmup_gaps_fail_even_when_evaluation_is_complete():
    index = pd.date_range("2024-01-01", periods=230, freq="1h", tz="UTC")
    data = frame(index).drop(index[50])
    report = inspect_frame(data, "1h", index[220], index[-1] + pd.Timedelta(hours=1), min_warmup=200)
    assert report["missing_bars"] == 0
    assert report["warmup_missing_bars"] == 1
    assert not report["ok"]


def test_full_research_runs_offline_without_inventing_a_winner(tmp_path):
    start = pd.Timestamp("2024-01-15", tz="UTC")
    end = start + pd.Timedelta(hours=3)
    frames = {tf: frame(pd.date_range(start - pd.Timedelta(hours=220), end, freq=freq, inclusive="left"))
              for tf, freq in [("1h", "1h"), ("15m", "15min"), ("5m", "5min")]}
    result = run_research(frames, "TEST", SymbolInfo("TEST", 1, 1, 1, 1, 100, 1, leverage=10),
                          start, end, tmp_path, calendar="24/7", folds=1)
    assert result["experiment_count"] == 10
    assert result["final_selection"] is None
    assert result["decision"] == "no_demonstrated_edge"
    assert (pd.read_csv(result["output"] + "/data/1h.csv").shape[0]) == len(frames["1h"])


def test_research_selects_before_holdout_and_logs_cost_stress(tmp_path):
    from backtest import research
    start = pd.Timestamp("2024-01-15", tz="UTC")
    end = start + pd.Timedelta(days=5)
    frames = {"1h": frame(pd.date_range(start, periods=2, freq="1h"))}
    calls = []
    class Replay:
        def __init__(self, *args, **kwargs):
            self.path = str(kwargs["artifact_dir"])
            self.cost = kwargs["cost_multiplier"]
        def run(self, **kwargs):
            calls.append((self.path, kwargs["start"], kwargs["end"]))
            return {"stats": {"total_trades": 40, "net_return": .01 / self.cost},
                    "trades": [{"pnl": 1.0}] * 40}
    with patch.object(research, "inspect_dataset", return_value={"ok": True, "timeframes": {}}), patch.object(research, "BacktestEngine", Replay):
        result = run_research(frames, "TEST", SymbolInfo("TEST", 1, 1, 1, 1, 100, 1), start, end, tmp_path, folds=1)
        repeated = run_research(frames, "TEST", SymbolInfo("TEST", 1, 1, 1, 1, 100, 1), start, end, tmp_path, folds=1)
    assert len(result["cost_stress"]) == 2
    assert not result["holdout_previously_evaluated"]
    assert repeated["holdout_previously_evaluated"]
    holdout = pd.Timestamp(result["final_holdout_start"])
    assert all(stop <= holdout for path, begin, stop in calls if "development-selection" in path or "-train-" in path)
    assert all(begin == holdout for path, begin, stop in calls if "final-holdout" in path)


def test_demo_report_does_not_claim_broker_execution(tmp_path):
    store = TradingStore(tmp_path / "demo.db")
    store.record_event("market_observation", {"valid": False})
    store.record_event("cycle_blocked", {"error": "future quote"})
    summary = export_demo_report(store, tmp_path / "report")
    assert summary["valid_quotes"] == 0
    assert summary["blocked_cycles"] == 1
    assert not summary["broker_orders_sent"]


def test_ambiguous_new_execution_is_quarantined_and_not_retried(tmp_path):
    from brokers.base import CriticalExecutionError
    from execution.service import SafeExecutionService
    from tests.test_safe_execution import FakeBroker, decision, signal
    broker = FakeBroker()
    broker.place_order = lambda request: (_ for _ in ()).throw(RuntimeError("timeout after submission"))
    store = TradingStore(tmp_path / "ambiguous.db")
    service = SafeExecutionService(broker, store)
    with pytest.raises(CriticalExecutionError, match="outcome unknown"):
        service.execute(signal(), decision())
    assert not service.entries_enabled
    assert store.get_state("pending_entry")
    service.reset_kill_switch()
    with pytest.raises(CriticalExecutionError, match="Unresolved entry"):
        service.execute(signal(), decision())


def test_failed_emergency_close_still_disables_entries(tmp_path):
    from brokers.base import CriticalExecutionError
    from execution.service import SafeExecutionService
    from tests.test_safe_execution import FakeBroker, decision, signal
    broker = FakeBroker(protected=False, attach=False)
    broker.close_position = lambda position: (_ for _ in ()).throw(RuntimeError("offline"))
    service = SafeExecutionService(broker, TradingStore(tmp_path / "emergency.db"))
    with pytest.raises(CriticalExecutionError):
        service.execute(signal(), decision())
    assert not service.entries_enabled
    event = service.store.recent_events(1, "critical_unprotected_position")[0]
    assert "offline" in event["payload"]["emergency_close"]["message"]


def test_post_fill_verification_outage_disables_and_closes(tmp_path):
    from brokers.base import CriticalExecutionError
    from execution.service import SafeExecutionService
    from tests.test_safe_execution import FakeBroker, decision, signal
    broker = FakeBroker()
    broker.get_open_positions = lambda symbol: (_ for _ in ()).throw(RuntimeError("position query offline"))
    service = SafeExecutionService(broker, TradingStore(tmp_path / "verify.db"))
    with pytest.raises(CriticalExecutionError):
        service.execute(signal(), decision())
    assert broker.closed
    assert not service.entries_enabled
