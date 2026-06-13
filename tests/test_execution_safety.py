import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from execution.executor import LiveTrader, PaperTrader


class ExecutionSafetyTests(unittest.TestCase):
    def test_live_trading_is_disabled_by_default(self):
        with mock.patch.object(config, "LIVE_TRADING_ENABLED", False):
            with self.assertRaises(RuntimeError):
                LiveTrader()

    def test_live_trading_requires_testnet_when_configured(self):
        with (
            mock.patch.object(config, "LIVE_TRADING_ENABLED", True),
            mock.patch.object(config, "REQUIRE_TESTNET_FOR_LIVE", True),
            mock.patch.object(config, "USE_TESTNET", False),
            mock.patch.object(config, "API_KEY", "key"),
            mock.patch.object(config, "API_SECRET", "secret"),
        ):
            with self.assertRaises(RuntimeError):
                LiveTrader()

    def test_paper_trader_persists_open_positions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "paper_state.json"
            trader = PaperTrader(initial_balance=1000.0, state_path=str(state_path))
            trader.open_position(
                {
                    "direction": "LONG",
                    "entry": 100.0,
                    "stop_loss": 95.0,
                    "take_profit": 110.0,
                    "size": 1.0,
                    "risk_amount": 5.0,
                    "rr_ratio": 2.0,
                },
                "BTC/USDT",
            )

            restored = PaperTrader(state_path=str(state_path))

            self.assertEqual(restored.balance, 1000.0)
            self.assertEqual(len(restored.positions), 1)
            self.assertEqual(restored.positions[0]["symbol"], "BTC/USDT")

    def test_paper_trader_blocks_daily_loss_cap(self):
        trader = PaperTrader(initial_balance=1000.0)
        trader.day_start_balance = 1000.0
        trader.balance = 960.0

        with mock.patch.object(config, "MAX_DAILY_LOSS_PCT", 0.03):
            self.assertFalse(trader.can_trade())


if __name__ == "__main__":
    unittest.main()
