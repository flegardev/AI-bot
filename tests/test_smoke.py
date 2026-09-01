import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

import config
import logger
from execution.executor import PaperTrader
from ml.predictor import MLPredictor
from web_demo import load_dashboard_data, page


class PaperTraderSessionTests(unittest.TestCase):
    def test_can_trade_resets_when_day_changes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "paper_state.json"
            trader = PaperTrader(initial_balance=1000, state_path=str(state_path))
            trader.session_trade_count = config.MAX_TRADES_PER_SESSION
            trader.session_day = trader.session_day - timedelta(days=1)

            self.assertTrue(trader.can_trade())
            self.assertEqual(trader.session_trade_count, 0)


class LoggerTests(unittest.TestCase):
    def test_log_trade_uses_configured_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "custom-trades.csv"
            with mock.patch.object(config, "TRADE_LOG_FILE", str(log_path)):
                logger.log_trade({"timestamp": "2026-01-01T00:00:00", "symbol": "BTC/USDT"})

            self.assertTrue(log_path.exists())
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("BTC/USDT", content)


class PredictorLoadTests(unittest.TestCase):
    def test_invalid_model_file_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "broken.pkl"
            model_path.write_text("not a pickle", encoding="utf-8")

            predictor = MLPredictor(model_path=str(model_path))

            self.assertFalse(predictor.is_trained)
            self.assertEqual(predictor.predict({}), 0.5)


class WebDemoRenderTests(unittest.TestCase):
    def test_page_renders_trading_bot_dashboard(self):
        data = load_dashboard_data()
        html = page(data)

        self.assertIn("<title>Trading Bot Dashboard</title>", html)
        self.assertIn("<h1>Trading Bot</h1>", html)
        self.assertIn("<h2>Backtest</h2>", html)
        self.assertIn("<h2>Account</h2>", html)
        self.assertIn("<h2>Operations</h2>", html)


if __name__ == "__main__":
    unittest.main()
