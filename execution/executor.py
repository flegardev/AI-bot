"""
execution/executor.py - Order execution for paper and live trading modes.

Paper trading simulates fills and tracks balance in memory.
Live trading places real Binance orders through ccxt.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import ccxt

import config
import logger
from data.fetcher import create_exchange


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PaperTrader:
    """Simulates order execution without touching the exchange."""

    def __init__(self, initial_balance: float = None, state_path: str = None):
        self.state_path = Path(state_path or config.PAPER_STATE_FILE)
        self.positions: list[dict] = []
        self.trades: list[dict] = []
        self.session_trade_count = 0
        self.session_day = _utc_now().date()
        self.balance = initial_balance or config.ACCOUNT_BALANCE
        self.day_start_balance = self.balance
        self.peak_balance = self.balance

        if initial_balance is None:
            self._load_state()

    def _roll_session_if_needed(self):
        today = _utc_now().date()
        if today != self.session_day:
            self.session_day = today
            self.session_trade_count = 0
            self.day_start_balance = self.balance
            self._save_state()

    def can_trade(self) -> bool:
        """Check we haven't hit the session trade cap."""
        self._roll_session_if_needed()
        if self.session_trade_count >= config.MAX_TRADES_PER_SESSION:
            return False
        if len(self.positions) >= config.MAX_OPEN_POSITIONS:
            return False
        if self.day_start_balance > 0:
            daily_loss_pct = max(0.0, (self.day_start_balance - self.balance) / self.day_start_balance)
            if daily_loss_pct >= config.MAX_DAILY_LOSS_PCT:
                logger.warning(f"Daily loss cap reached ({daily_loss_pct:.1%}).")
                return False
        if self.peak_balance > 0:
            drawdown_pct = max(0.0, (self.peak_balance - self.balance) / self.peak_balance)
            if drawdown_pct >= config.MAX_DRAWDOWN_PCT:
                logger.warning(f"Max drawdown cap reached ({drawdown_pct:.1%}).")
                return False
        return True

    def _load_state(self):
        if not self.state_path.exists():
            return
        try:
            with self.state_path.open("r", encoding="utf-8") as f:
                state = json.load(f)
            self.balance = float(state.get("balance", self.balance))
            self.positions = state.get("positions", [])
            self.trades = state.get("trades", [])
            self.session_trade_count = int(state.get("session_trade_count", 0))
            self.session_day = datetime.fromisoformat(state.get("session_day")).date()
            self.day_start_balance = float(state.get("day_start_balance", self.balance))
            self.peak_balance = float(state.get("peak_balance", self.balance))
            logger.info(f"Loaded paper state from {self.state_path}")
        except Exception as e:
            logger.warning(f"Could not load paper state from {self.state_path}: {e}")

    def _save_state(self):
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "balance": self.balance,
                "positions": self.positions,
                "trades": self.trades,
                "session_trade_count": self.session_trade_count,
                "session_day": self.session_day.isoformat(),
                "day_start_balance": self.day_start_balance,
                "peak_balance": self.peak_balance,
            }
            with self.state_path.open("w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save paper state to {self.state_path}: {e}")

    def open_position(self, trade_params: dict, symbol: str, confidence: float = None) -> dict | None:
        """Enter a paper trade."""
        if not self.can_trade():
            logger.warning(f"Session trade cap ({config.MAX_TRADES_PER_SESSION}) reached.")
            return None

        trade = {
            "id": len(self.trades) + len(self.positions) + 1,
            "timestamp": _utc_now().isoformat(),
            "symbol": symbol,
            "direction": trade_params["direction"],
            "entry_price": trade_params["entry"],
            "stop_loss": trade_params["stop_loss"],
            "take_profit": trade_params["take_profit"],
            "size": trade_params["size"],
            "risk_amount": trade_params["risk_amount"],
            "rr_ratio": trade_params["rr_ratio"],
            "confidence": confidence,
            "status": "open",
            "mode": "paper",
        }

        self.positions.append(trade)
        self.session_trade_count += 1
        self._save_state()

        logger.trade_signal(
            direction=trade["direction"],
            symbol=symbol,
            entry=trade["entry_price"],
            sl=trade["stop_loss"],
            tp=trade["take_profit"],
            size=trade["size"],
            confidence=confidence,
        )

        return trade

    def update_positions(self, current_price: float):
        """Check open positions against the current price and close any hits."""
        still_open = []
        for pos in self.positions:
            result = self._check_exit(pos, current_price)
            if result:
                self._close_position(pos, result["exit_price"], result["result"])
            else:
                still_open.append(pos)
        self.positions = still_open
        self._save_state()

    def get_balance(self) -> float:
        return self.balance

    def _check_exit(self, pos: dict, price: float) -> dict | None:
        """Return exit info if price hit SL or TP, else None."""
        if pos["direction"] == "LONG":
            if price <= pos["stop_loss"]:
                return {"exit_price": pos["stop_loss"], "result": "loss"}
            if price >= pos["take_profit"]:
                return {"exit_price": pos["take_profit"], "result": "win"}
        else:
            if price >= pos["stop_loss"]:
                return {"exit_price": pos["stop_loss"], "result": "loss"}
            if price <= pos["take_profit"]:
                return {"exit_price": pos["take_profit"], "result": "win"}
        return None

    def _close_position(self, pos: dict, exit_price: float, result: str):
        """Calculate PnL, update balance, and log the trade."""
        entry = pos["entry_price"]
        size = pos["size"]

        if pos["direction"] == "LONG":
            pnl = (exit_price - entry) * size
        else:
            pnl = (entry - exit_price) * size

        pnl_pct = pnl / self.balance * 100
        self.balance += pnl
        self.peak_balance = max(self.peak_balance, self.balance)

        closed = {
            **pos,
            "exit_price": exit_price,
            "pnl": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 4),
            "result": result,
            "status": "closed",
        }

        self.trades.append(closed)
        logger.log_trade(closed)

        color_fn = logger.success if result == "win" else logger.warning
        color_fn(
            f"[{pos['direction']}] {result.upper()}  "
            f"entry={entry:.4f}  exit={exit_price:.4f}  "
            f"PnL=${pnl:+.2f} ({pnl_pct:+.2f}%)  "
            f"Balance=${self.balance:,.2f}"
        )

    def session_stats(self) -> dict:
        closed = self.trades
        wins = [t for t in closed if t["result"] == "win"]
        total_pnl = sum(t["pnl"] for t in closed)
        return {
            "trades": len(closed),
            "wins": len(wins),
            "losses": len(closed) - len(wins),
            "win_rate": len(wins) / len(closed) if closed else 0,
            "pnl": total_pnl,
            "balance": self.balance,
        }


class LiveTrader:
    """Executes real orders on Binance via ccxt."""

    def __init__(self):
        self._validate_live_config()
        self.exchange = self._build_trading_exchange()
        self.open_orders: list[dict] = []
        self.session_trade_count = 0
        self.session_day = _utc_now().date()
        self.day_start_balance = self._safe_balance()
        self.peak_balance = self.day_start_balance
        self._reconcile_existing_orders()

    def _validate_live_config(self):
        if not config.LIVE_TRADING_ENABLED:
            raise RuntimeError(
                "Live trading is disabled. Set LIVE_TRADING_ENABLED=true only after testnet validation."
            )
        if config.REQUIRE_TESTNET_FOR_LIVE and not config.USE_TESTNET:
            raise RuntimeError(
                "Live trading requires USE_TESTNET=true while REQUIRE_TESTNET_FOR_LIVE=true."
            )
        if not config.API_KEY or not config.API_SECRET:
            raise RuntimeError("Live trading requires BINANCE_API_KEY and BINANCE_API_SECRET.")

    def _build_trading_exchange(self):
        exchange = create_exchange(include_credentials=True, sandbox=config.USE_TESTNET)

        if config.USE_TESTNET:
            logger.info("Binance sandbox mode enabled for live executor.")

        return exchange

    def _reconcile_existing_orders(self):
        try:
            open_orders = self.exchange.fetch_open_orders()
        except Exception as e:
            logger.warning(f"Could not reconcile existing live orders: {e}")
            return
        if open_orders:
            self.open_orders = open_orders
            logger.warning(
                f"Found {len(open_orders)} existing open exchange order(s). "
                "Review them before allowing new live entries."
            )

    def _safe_balance(self) -> float:
        try:
            return self.get_balance()
        except Exception as e:
            logger.warning(f"Could not fetch live balance: {e}")
            return 0.0

    def _roll_session_if_needed(self):
        today = _utc_now().date()
        if today != self.session_day:
            self.session_day = today
            self.session_trade_count = 0
            self.day_start_balance = self._safe_balance()

    def can_trade(self) -> bool:
        self._roll_session_if_needed()
        if self.session_trade_count >= config.MAX_TRADES_PER_SESSION:
            return False
        if len(self.open_orders) >= config.MAX_OPEN_POSITIONS:
            return False
        current_balance = self._safe_balance()
        self.peak_balance = max(self.peak_balance, current_balance)
        if self.day_start_balance > 0:
            daily_loss_pct = max(0.0, (self.day_start_balance - current_balance) / self.day_start_balance)
            if daily_loss_pct >= config.MAX_DAILY_LOSS_PCT:
                logger.warning(f"Daily loss cap reached ({daily_loss_pct:.1%}).")
                return False
        if self.peak_balance > 0:
            drawdown_pct = max(0.0, (self.peak_balance - current_balance) / self.peak_balance)
            if drawdown_pct >= config.MAX_DRAWDOWN_PCT:
                logger.warning(f"Max drawdown cap reached ({drawdown_pct:.1%}).")
                return False
        return True

    def get_balance(self) -> float:
        """Fetch live USDT balance from exchange."""
        balance = self.exchange.fetch_balance()
        usdt = balance.get("USDT", {})
        free_balance = usdt.get("free", balance.get("free", {}).get("USDT", 0.0))
        return float(free_balance or 0.0)

    def open_position(self, trade_params: dict, symbol: str, confidence: float = None) -> dict | None:
        """Place a live market order with stop-loss and take-profit orders."""
        if not self.can_trade():
            logger.warning("Session trade cap reached - no live order placed.")
            return None

        direction = trade_params["direction"]
        if direction == "SHORT":
            logger.error("SHORT live trades are not supported on the current Binance spot setup.")
            return None

        size = trade_params["size"]
        sl = trade_params["stop_loss"]
        tp = trade_params["take_profit"]

        try:
            self.exchange.load_markets()
            market = self.exchange.market(symbol)
            size = float(self.exchange.amount_to_precision(symbol, size))
            sl = float(self.exchange.price_to_precision(symbol, sl))
            tp = float(self.exchange.price_to_precision(symbol, tp))
            min_amount = (market.get("limits", {}).get("amount", {}) or {}).get("min")
            min_cost = (market.get("limits", {}).get("cost", {}) or {}).get("min")
            if min_amount and size < min_amount:
                raise ValueError(f"Order size {size} below exchange minimum amount {min_amount}.")
            if min_cost and size * trade_params["entry"] < min_cost:
                raise ValueError(f"Order notional {size * trade_params['entry']:.4f} below minimum {min_cost}.")

            side = "buy" if direction == "LONG" else "sell"
            order = self.exchange.create_market_order(symbol, side, size)
            fill_price = float(order["price"] or order["average"] or trade_params["entry"])

            logger.success(f"LIVE ORDER placed: {direction} {size} {symbol} @ {fill_price}")

            exit_side = "sell" if direction == "LONG" else "buy"
            tp_order = self.exchange.create_limit_order(symbol, exit_side, size, tp)
            sl_order = self.exchange.create_order(
                symbol,
                "STOP_LOSS_LIMIT",
                exit_side,
                size,
                sl,
                params={"stopPrice": sl, "timeInForce": "GTC"},
            )

            trade = {
                "timestamp": _utc_now().isoformat(),
                "symbol": symbol,
                "direction": direction,
                "entry_price": fill_price,
                "stop_loss": sl,
                "take_profit": tp,
                "size": size,
                "risk_amount": trade_params["risk_amount"],
                "rr_ratio": trade_params["rr_ratio"],
                "confidence": confidence,
                "entry_order_id": order["id"],
                "tp_order_id": tp_order["id"],
                "sl_order_id": sl_order["id"],
                "mode": "live",
                "status": "open",
            }

            self.open_orders.append(trade)
            self.session_trade_count += 1
            logger.log_trade(trade)
            logger.trade_signal(direction, symbol, fill_price, sl, tp, size, confidence)
            return trade

        except Exception as e:
            logger.error(f"Failed to place live order: {e}")
            return None


def create_executor(mode: str = None, initial_balance: float = None):
    """Return the appropriate trader for the current mode."""
    mode = mode or config.MODE
    if mode == "live":
        logger.warning("LIVE trading mode - real orders will be placed!")
        return LiveTrader()
    return PaperTrader(initial_balance)
