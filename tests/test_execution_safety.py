import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from execution.executor import LiveSafetyError, LiveTrader, PaperTrader


class FakeExchange:
    """Small stateful exchange double for live-order failure paths."""

    def __init__(
        self,
        *,
        balance_failure: bool = False,
        reconciliation_failure: bool = False,
        entry_failure: bool = False,
        entry_failure_after_fill: bool = False,
        take_profit_failure: bool = False,
        stop_loss_failure: bool = False,
        close_failure: bool = False,
    ):
        self.balance_failure = balance_failure
        self.reconciliation_failure = reconciliation_failure
        self.entry_failure = entry_failure
        self.entry_failure_after_fill = entry_failure_after_fill
        self.take_profit_failure = take_profit_failure
        self.stop_loss_failure = stop_loss_failure
        self.close_failure = close_failure
        self.free = {"USDT": 10_000.0, "BTC": 0.0}
        self.open_orders = []
        self.market_orders = []
        self.cancelled_order_ids = []
        self._next_id = 1

    def _id(self) -> str:
        value = str(self._next_id)
        self._next_id += 1
        return value

    def fetch_balance(self):
        if self.balance_failure:
            raise RuntimeError("balance endpoint unavailable")
        return {
            asset: {"free": value} for asset, value in self.free.items()
        } | {"free": dict(self.free)}

    def fetch_open_orders(self, _symbol=None):
        if self.reconciliation_failure:
            raise RuntimeError("open-order endpoint unavailable")
        return list(self.open_orders)

    def load_markets(self):
        return None

    def market(self, _symbol):
        return {
            "id": "BTCUSDT",
            "base": "BTC",
            "limits": {"amount": {"min": 0.0001}, "cost": {"min": 5.0}},
        }

    def amount_to_precision(self, _symbol, amount):
        return f"{float(amount):.6f}"

    def price_to_precision(self, _symbol, price):
        return f"{float(price):.2f}"

    def create_market_order(self, symbol, side, amount, params=None):
        amount = float(amount)
        if side == "sell" and self.close_failure:
            raise RuntimeError("compensating close rejected")
        if side == "buy" and (self.entry_failure or self.entry_failure_after_fill):
            if self.entry_failure_after_fill:
                self.free["BTC"] += amount
            raise RuntimeError("entry submission timed out")
        if side == "buy":
            self.free["BTC"] += amount
        else:
            self.free["BTC"] = max(0.0, self.free["BTC"] - amount)
        order = {
            "id": self._id(),
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "filled": amount,
            "average": 100.0,
            "clientOrderId": (params or {}).get("newClientOrderId"),
        }
        self.market_orders.append(order)
        return order

    def create_limit_order(self, symbol, side, amount, price):
        if self.take_profit_failure:
            raise RuntimeError("take-profit rejected")
        order = {
            "id": self._id(),
            "symbol": symbol,
            "side": side,
            "amount": float(amount),
            "price": float(price),
        }
        self.open_orders.append(order)
        return order

    def create_order(self, symbol, order_type, side, amount, price, params=None):
        if self.stop_loss_failure:
            raise RuntimeError("stop-loss rejected")
        order = {
            "id": self._id(),
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "amount": float(amount),
            "price": price,
            "params": params or {},
        }
        self.open_orders.append(order)
        return order

    def cancel_order(self, order_id, _symbol):
        self.cancelled_order_ids.append(str(order_id))
        self.open_orders = [order for order in self.open_orders if str(order["id"]) != str(order_id)]


class FakeOcoExchange(FakeExchange):
    def __init__(self):
        super().__init__()
        self.oco_calls = []

    def private_post_orderlist_oco(self, params):
        self.oco_calls.append(params)
        return {
            "orderListId": 77,
            "orders": [{"orderId": 101}, {"orderId": 102}],
        }


def live_config():
    return mock.patch.multiple(
        config,
        LIVE_TRADING_ENABLED=True,
        REQUIRE_TESTNET_FOR_LIVE=True,
        USE_TESTNET=True,
        API_KEY="test-key",  # pragma: allowlist secret
        API_SECRET="test-secret",  # pragma: allowlist secret
    )


def trade_params():
    return {
        "direction": "LONG",
        "entry": 100.0,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "size": 1.0,
        "risk_amount": 5.0,
        "rr_ratio": 2.0,
    }


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

    def test_live_balance_failure_blocks_startup_without_demo_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir, live_config():
            exchange = FakeExchange(balance_failure=True)
            with self.assertRaisesRegex(LiveSafetyError, "live USDT balance"):
                LiveTrader(exchange=exchange, state_path=str(Path(tmpdir) / "live.json"))

    def test_live_reconciliation_failure_blocks_startup(self):
        with tempfile.TemporaryDirectory() as tmpdir, live_config():
            exchange = FakeExchange(reconciliation_failure=True)
            with self.assertRaisesRegex(LiveSafetyError, "reconcile existing live orders"):
                LiveTrader(exchange=exchange, state_path=str(Path(tmpdir) / "live.json"))

    def test_ambiguous_entry_failure_closes_detected_fill_and_journals_it(self):
        with tempfile.TemporaryDirectory() as tmpdir, live_config():
            state_path = Path(tmpdir) / "live.json"
            exchange = FakeExchange(entry_failure_after_fill=True)
            trader = LiveTrader(exchange=exchange, state_path=str(state_path))

            with self.assertRaisesRegex(LiveSafetyError, "Entry submission failed"):
                trader.open_position(trade_params(), "BTC/USDT")

            self.assertEqual(exchange.free["BTC"], 0.0)
            self.assertEqual([order["side"] for order in exchange.market_orders], ["sell"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["records"][-1]["status"], "compensated")
            self.assertIn("entry_error", state["records"][-1])

    def test_take_profit_failure_market_closes_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir, live_config():
            state_path = Path(tmpdir) / "live.json"
            exchange = FakeExchange(take_profit_failure=True)
            trader = LiveTrader(exchange=exchange, state_path=str(state_path))

            with self.assertRaisesRegex(LiveSafetyError, "Take-profit placement failed"):
                trader.open_position(trade_params(), "BTC/USDT")

            self.assertEqual(exchange.free["BTC"], 0.0)
            self.assertEqual([order["side"] for order in exchange.market_orders], ["buy", "sell"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["records"][-1]["status"], "compensated")

    def test_stop_loss_failure_cancels_take_profit_then_closes_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir, live_config():
            state_path = Path(tmpdir) / "live.json"
            exchange = FakeExchange(stop_loss_failure=True)
            trader = LiveTrader(exchange=exchange, state_path=str(state_path))

            with self.assertRaisesRegex(LiveSafetyError, "Stop-loss placement failed"):
                trader.open_position(trade_params(), "BTC/USDT")

            self.assertEqual(exchange.free["BTC"], 0.0)
            self.assertEqual(exchange.open_orders, [])
            self.assertEqual(exchange.cancelled_order_ids, ["2"])
            self.assertEqual([order["side"] for order in exchange.market_orders], ["buy", "sell"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["records"][-1]["status"], "compensated")

    def test_binance_oco_path_places_both_exit_legs_atomically(self):
        with tempfile.TemporaryDirectory() as tmpdir, live_config():
            state_path = Path(tmpdir) / "live.json"
            exchange = FakeOcoExchange()
            trader = LiveTrader(exchange=exchange, state_path=str(state_path))

            trade = trader.open_position(trade_params(), "BTC/USDT")

            self.assertIsNotNone(trade)
            self.assertEqual(len(exchange.oco_calls), 1)
            self.assertEqual(exchange.oco_calls[0]["aboveType"], "LIMIT_MAKER")
            self.assertEqual(exchange.oco_calls[0]["belowType"], "STOP_LOSS")
            self.assertEqual(trade["tp_order_id"], "101")
            self.assertEqual(trade["sl_order_id"], "102")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["records"][-1]["status"], "protected")
            self.assertEqual(state["records"][-1]["protection_mode"], "binance_oco")

    def test_incomplete_compensation_is_durable_and_blocks_restart(self):
        with tempfile.TemporaryDirectory() as tmpdir, live_config():
            state_path = Path(tmpdir) / "live.json"
            exchange = FakeExchange(take_profit_failure=True, close_failure=True)
            trader = LiveTrader(exchange=exchange, state_path=str(state_path))

            with self.assertRaisesRegex(LiveSafetyError, "Automatic compensation was incomplete"):
                trader.open_position(trade_params(), "BTC/USDT")

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                state["records"][-1]["status"], "manual_reconciliation_required"
            )
            with self.assertRaisesRegex(LiveSafetyError, "manual exchange reconciliation"):
                LiveTrader(exchange=exchange, state_path=str(state_path))

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
