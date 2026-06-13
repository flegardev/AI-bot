import unittest
from unittest import mock

import pandas as pd

import config
from risk.manager import calculate_position
from strategy.bias import get_bias
from strategy.entry import detect_entry_signal
from strategy.liquidity import detect_liquidity_sweep


def _ohlcv(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df.index = pd.date_range("2026-01-01", periods=len(df), freq="5min", tz="UTC")
    return df


class StrategyAndRiskTests(unittest.TestCase):
    def test_bias_detects_long_when_price_above_ema(self):
        df = _ohlcv(
            [
                {"open": i, "high": i + 1, "low": i - 1, "close": float(i), "volume": 1.0}
                for i in range(1, config.EMA_PERIOD + 51)
            ]
        )

        self.assertEqual(get_bias(df), "LONG")

    def test_liquidity_sweep_detects_bullish_equal_low_sweep(self):
        rows = [
            {"open": 101.5, "high": 102.0, "low": 101.0, "close": 101.5, "volume": 1.0}
            for _ in range(30)
        ]
        rows[6]["low"] = 100.0
        rows[16]["low"] = 100.05
        rows[-1] = {"open": 100.4, "high": 101.0, "low": 99.8, "close": 100.3, "volume": 1.0}

        result = detect_liquidity_sweep(_ohlcv(rows), lookback=30)

        self.assertTrue(result["swept"])
        self.assertEqual(result["direction"], "bullish")

    def test_entry_signal_detects_bullish_bos(self):
        rows = [
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0}
            for _ in range(35)
        ]
        rows[8]["high"] = 104.0
        rows[18]["low"] = 97.0
        rows[24]["high"] = 103.0
        rows[-1]["close"] = 105.0
        rows[-1]["high"] = 105.5

        result = detect_entry_signal(_ohlcv(rows), "bullish", lookback=35)

        self.assertTrue(result["signal"])
        self.assertEqual(result["type"], "BOS")

    def test_position_rejects_stop_on_wrong_side(self):
        with mock.patch.object(config, "RISK_PER_TRADE", 0.01):
            self.assertIsNone(calculate_position("LONG", 100.0, 101.0, 10_000.0))
            self.assertIsNone(calculate_position("SHORT", 100.0, 99.0, 10_000.0))

    def test_position_sizes_from_risk_amount(self):
        trade = calculate_position("LONG", 100.0, 95.0, 10_000.0, risk_pct=0.01, min_rr=2.0)

        self.assertEqual(trade["size"], 20.0)
        self.assertEqual(trade["risk_amount"], 100.0)
        self.assertEqual(trade["take_profit"], 110.0)


if __name__ == "__main__":
    unittest.main()
