"""
execution/executor.py - Order execution for paper and live trading modes.

Paper trading simulates fills and tracks balance in memory.
Live trading places real Binance orders through ccxt.
"""
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import config
import logger
from data.fetcher import create_exchange


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LiveSafetyError(RuntimeError):
    """A live-trading invariant failed and new real orders must stop."""


class ProtectionPlacementError(RuntimeError):
    """Exit protection failed after an entry was accepted."""

    def __init__(self, message: str, created_orders: list[dict] | None = None):
        super().__init__(message)
        self.created_orders = created_orders or []


class PaperTrader:
    """Simulates order execution without touching the exchange."""

    def __init__(self, initial_balance: float | None = None, state_path: str | None = None):
        self.state_path = Path(state_path or config.PAPER_STATE_FILE)
        self.positions: list[dict] = []
        self.trades: list[dict] = []
        self.session_trade_count = 0
        self.session_day = _utc_now().date()
        self.balance = config.ACCOUNT_BALANCE if initial_balance is None else initial_balance
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

    def open_position(
        self, trade_params: dict, symbol: str, confidence: float | None = None
    ) -> dict | None:
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
    """Executes real Binance spot orders with fail-closed safety checks."""

    _UNRESOLVED_STATES: ClassVar[set[str]] = {
        "prepared",
        "entry_submitting",
        "entry_filled",
        "protection_pending",
        "manual_reconciliation_required",
    }

    def __init__(self, exchange=None, state_path: str | None = None):
        self._validate_live_config()
        self.exchange = exchange or self._build_trading_exchange()
        self.state_path = Path(state_path or config.LIVE_STATE_FILE)
        self._live_state = self._load_live_state()
        self.open_orders: list[dict] = []
        self.session_trade_count = 0
        self.session_day = _utc_now().date()
        self.blocked_reason: str | None = None
        self._assert_no_unresolved_state()
        self.day_start_balance = self.get_balance()
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

    def _load_live_state(self) -> dict:
        if not self.state_path.exists():
            return {"version": 1, "records": []}
        try:
            with self.state_path.open("r", encoding="utf-8") as f:
                state = json.load(f)
            if not isinstance(state, dict) or not isinstance(state.get("records"), list):
                raise ValueError("expected an object containing a records list")
            return state
        except Exception as e:
            raise LiveSafetyError(
                f"Live reconciliation state is unreadable ({self.state_path}): {e}. "
                "No live order will be submitted."
            ) from e

    def _save_live_state(self) -> None:
        """Atomically persist live actions before or immediately after exchange calls."""
        temp_path = self.state_path.with_suffix(f"{self.state_path.suffix}.tmp")
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with temp_path.open("w", encoding="utf-8") as f:
                json.dump(self._live_state, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            temp_path.replace(self.state_path)
        except Exception as e:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise LiveSafetyError(
                f"Could not durably write live reconciliation state ({self.state_path}): {e}"
            ) from e

    def _assert_no_unresolved_state(self) -> None:
        unresolved = [
            record
            for record in self._live_state["records"]
            if record.get("status") in self._UNRESOLVED_STATES
        ]
        if unresolved:
            identifiers = ", ".join(str(record.get("client_order_id", "unknown")) for record in unresolved)
            raise LiveSafetyError(
                "Unresolved live action(s) require manual exchange reconciliation before restart: "
                f"{identifiers}. See {self.state_path}."
            )

    def _new_record(self, symbol: str, size: float, base_balance_before: float) -> dict:
        record = {
            "client_order_id": f"aibot{uuid4().hex[:20]}",
            "created_at": _utc_now().isoformat(),
            "updated_at": _utc_now().isoformat(),
            "symbol": symbol,
            "requested_size": size,
            "base_balance_before": base_balance_before,
            "status": "prepared",
        }
        self._live_state["records"].append(record)
        self._save_live_state()
        return record

    def _update_record(self, record: dict, status: str, **details) -> None:
        record.update(details)
        record["status"] = status
        record["updated_at"] = _utc_now().isoformat()
        self._save_live_state()

    def _block(self, reason: str) -> LiveSafetyError:
        self.blocked_reason = reason
        logger.error(f"LIVE SAFETY BLOCK: {reason}")
        return LiveSafetyError(reason)

    def _reconcile_existing_orders(self):
        try:
            open_orders = self.exchange.fetch_open_orders()
        except Exception as e:
            raise self._block(f"Could not reconcile existing live orders: {e}") from e
        if not isinstance(open_orders, list):
            raise self._block("Exchange returned an invalid open-order reconciliation response.")
        self.open_orders = open_orders
        if open_orders:
            logger.warning(
                f"Found {len(open_orders)} existing open exchange order(s). "
                "Review them before allowing new live entries."
            )

    def _roll_session_if_needed(self):
        today = _utc_now().date()
        if today != self.session_day:
            self.session_day = today
            self.session_trade_count = 0
            self.day_start_balance = self.get_balance()

    def can_trade(self) -> bool:
        if self.blocked_reason:
            return False
        self._roll_session_if_needed()
        self._reconcile_existing_orders()
        if self.session_trade_count >= config.MAX_TRADES_PER_SESSION:
            return False
        if len(self.open_orders) >= config.MAX_OPEN_POSITIONS:
            return False
        current_balance = self.get_balance()
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
        """Fetch a valid positive live USDT balance; never use a configured fallback."""
        try:
            balance = self.exchange.fetch_balance()
            usdt = balance.get("USDT", {})
            value = usdt.get("free", balance.get("free", {}).get("USDT"))
            free_balance = float(value)
        except Exception as e:
            raise self._block(f"Could not fetch live USDT balance: {e}") from e
        if not math.isfinite(free_balance) or free_balance <= 0:
            raise self._block(
                f"Live USDT balance must be positive and finite; exchange returned {free_balance!r}."
            )
        return free_balance

    def _get_free_asset_balance(self, asset: str) -> float:
        try:
            balance = self.exchange.fetch_balance()
            asset_data = balance.get(asset, {})
            value = asset_data.get("free", balance.get("free", {}).get(asset))
            free_balance = float(value or 0.0)
        except Exception as e:
            raise LiveSafetyError(f"Could not reconcile free {asset} balance: {e}") from e
        if not math.isfinite(free_balance) or free_balance < 0:
            raise LiveSafetyError(f"Exchange returned invalid free {asset} balance: {free_balance!r}")
        return free_balance

    @staticmethod
    def _order_id(order: dict | None) -> str | None:
        if not isinstance(order, dict):
            return None
        value = order.get("id") or order.get("orderId")
        return str(value) if value is not None else None

    @staticmethod
    def _order_client_id(order: dict | None) -> str | None:
        if not isinstance(order, dict):
            return None
        raw_info = order.get("info")
        info = raw_info if isinstance(raw_info, dict) else {}
        value = order.get("clientOrderId") or info.get("clientOrderId")
        return str(value) if value is not None else None

    def _place_protection(
        self,
        symbol: str,
        market: dict,
        size: float,
        stop_loss: float,
        take_profit: float,
        client_order_id: str,
    ) -> dict:
        """Use Binance's atomic OCO endpoint when exposed by the pinned adapter."""
        oco_method = getattr(self.exchange, "private_post_orderlist_oco", None)
        if callable(oco_method):
            params = {
                "symbol": market["id"],
                "side": "SELL",
                "quantity": self.exchange.amount_to_precision(symbol, size),
                "listClientOrderId": f"{client_order_id}o",
                "aboveType": "LIMIT_MAKER",
                "abovePrice": self.exchange.price_to_precision(symbol, take_profit),
                "aboveClientOrderId": f"{client_order_id}t",
                "belowType": "STOP_LOSS",
                "belowStopPrice": self.exchange.price_to_precision(symbol, stop_loss),
                "belowClientOrderId": f"{client_order_id}s",
                "newOrderRespType": "RESULT",
            }
            try:
                response = oco_method(params)
            except Exception as e:
                raise ProtectionPlacementError(f"Atomic Binance OCO protection failed: {e}") from e
            if not isinstance(response, dict) or response.get("orderListId") is None:
                raise ProtectionPlacementError(
                    "Atomic Binance OCO returned an invalid/ambiguous response"
                )
            orders = response.get("orders", []) if isinstance(response, dict) else []
            reports = response.get("orderReports", []) if isinstance(response, dict) else []
            leg_ids = [self._order_id(order) for order in (orders or reports)]
            leg_ids = [order_id for order_id in leg_ids if order_id]
            return {
                "mode": "binance_oco",
                "order_list_id": response.get("orderListId") if isinstance(response, dict) else None,
                "tp_order_id": leg_ids[0] if leg_ids else None,
                "sl_order_id": leg_ids[1] if len(leg_ids) > 1 else None,
                "created_orders": [],
            }

        # Portable fallback for adapters without an OCO endpoint. If either leg
        # fails, the caller cancels every created exit and market-closes the entry.
        created_orders: list[dict] = []
        try:
            tp_order = self.exchange.create_limit_order(symbol, "sell", size, take_profit)
            created_orders.append(tp_order)
        except Exception as e:
            raise ProtectionPlacementError(f"Take-profit placement failed: {e}") from e
        try:
            sl_order = self.exchange.create_order(
                symbol,
                "STOP_LOSS",
                "sell",
                size,
                None,
                params={"stopPrice": stop_loss},
            )
            created_orders.append(sl_order)
        except Exception as e:
            raise ProtectionPlacementError(
                f"Stop-loss placement failed: {e}", created_orders=created_orders
            ) from e
        return {
            "mode": "separate_exit_orders",
            "order_list_id": None,
            "tp_order_id": self._order_id(tp_order),
            "sl_order_id": self._order_id(sl_order),
            "created_orders": created_orders,
        }

    def _cancel_created_orders(self, symbol: str, orders: list[dict]) -> list[str]:
        errors = []
        for order in orders:
            order_id = self._order_id(order)
            if not order_id:
                errors.append("created exit order had no ID")
                continue
            try:
                self.exchange.cancel_order(order_id, symbol)
            except Exception as e:
                try:
                    still_open = any(
                        self._order_id(open_order) == order_id
                        for open_order in self.exchange.fetch_open_orders(symbol)
                    )
                except Exception as reconcile_error:
                    errors.append(
                        f"cancel {order_id}: {e}; could not verify cancellation: {reconcile_error}"
                    )
                else:
                    if still_open:
                        errors.append(f"cancel {order_id}: {e}")
        return errors

    def _compensate_entry(
        self,
        record: dict,
        symbol: str,
        base_asset: str,
        base_balance_before: float,
        expected_size: float,
        created_orders: list[dict] | None = None,
    ) -> None:
        """Cancel partial exits and close only the newly acquired free base asset."""
        candidate_orders = list(created_orders or [])
        errors = []
        known_ids = {self._order_id(order) for order in candidate_orders}
        known_ids.discard(None)
        try:
            open_orders = self.exchange.fetch_open_orders(symbol)
            candidate_orders.extend(
                order
                for order in open_orders
                if self._order_id(order) in known_ids
                or (self._order_client_id(order) or "").startswith(record["client_order_id"])
            )
        except Exception as e:
            errors.append(f"open-order reconciliation before cancellation: {e}")

        unique_orders = {}
        for order in candidate_orders:
            order_id = self._order_id(order)
            if order_id:
                unique_orders[order_id] = order
        errors.extend(self._cancel_created_orders(symbol, list(unique_orders.values())))

        try:
            open_orders = self.exchange.fetch_open_orders(symbol)
            remaining = [
                order
                for order in open_orders
                if self._order_id(order) in unique_orders
                or (self._order_client_id(order) or "").startswith(record["client_order_id"])
            ]
            if remaining:
                errors.append("one or more entry/protection orders remain open")
        except Exception as e:
            errors.append(f"open-order reconciliation after cancellation: {e}")

        close_order = None
        close_size = 0.0
        try:
            current_base = self._get_free_asset_balance(base_asset)
            acquired = max(0.0, current_base - base_balance_before)
            close_size = min(expected_size, acquired)
            close_size = float(self.exchange.amount_to_precision(symbol, close_size))
            if close_size > 0:
                close_order = self.exchange.create_market_order(
                    symbol,
                    "sell",
                    close_size,
                    params={"newClientOrderId": f"{record['client_order_id']}c"},
                )
                reported_filled = close_order.get("filled") if isinstance(close_order, dict) else None
                if reported_filled is not None and float(reported_filled) + 1e-12 < close_size:
                    errors.append(
                        f"compensating market close only reported {reported_filled} of {close_size} filled"
                    )
            remaining_base = self._get_free_asset_balance(base_asset)
            remaining_acquired = max(0.0, remaining_base - base_balance_before)
            remaining_precise = float(
                self.exchange.amount_to_precision(symbol, remaining_acquired)
            )
            if remaining_precise > 0:
                errors.append(
                    f"{remaining_precise} {base_asset} from the entry remains unclosed"
                )
        except Exception as e:
            errors.append(f"compensating market close: {e}")

        if errors:
            self._update_record(
                record,
                "manual_reconciliation_required",
                reconciliation_errors=errors,
                compensating_order_id=self._order_id(close_order),
                compensating_size=close_size,
            )
            raise self._block(
                "Automatic compensation was incomplete. Inspect the exchange and the live "
                f"reconciliation journal at {self.state_path}: {'; '.join(errors)}"
            )

        self._update_record(
            record,
            "compensated",
            compensating_order_id=self._order_id(close_order),
            compensating_size=close_size,
        )

    def _handle_ambiguous_entry_failure(
        self,
        record: dict,
        symbol: str,
        base_asset: str,
        base_balance_before: float,
        expected_size: float,
        error: Exception,
    ) -> None:
        """Resolve an entry call that raised after its acceptance status became unknown."""
        try:
            open_orders = self.exchange.fetch_open_orders(symbol)
            matching = [
                order
                for order in open_orders
                if order.get("clientOrderId") == record["client_order_id"]
                or (order.get("info") or {}).get("clientOrderId") == record["client_order_id"]
            ]
        except Exception as reconcile_error:
            self._update_record(
                record,
                "manual_reconciliation_required",
                entry_error=str(error),
                reconciliation_errors=[str(reconcile_error)],
            )
            raise self._block(
                "Entry submission failed ambiguously and open-order reconciliation also failed. "
                f"Inspect {self.state_path}."
            ) from reconcile_error

        self._compensate_entry(
            record,
            symbol,
            base_asset,
            base_balance_before,
            expected_size,
            created_orders=matching,
        )
        record["entry_error"] = str(error)
        self._save_live_state()
        raise self._block("Entry submission failed; exchange state was reconciled and any fill was closed.")

    def open_position(
        self, trade_params: dict, symbol: str, confidence: float | None = None
    ) -> dict | None:
        """Place a live market entry and protect it, or compensate and stop."""
        if not self.can_trade():
            logger.warning("Live safety/risk gate blocked this entry; no order was placed.")
            return None

        direction = trade_params["direction"]
        if direction != "LONG":
            logger.error(
                f"{direction} live trades are not supported on the current Binance spot setup."
            )
            return None

        size = trade_params["size"]
        sl = trade_params["stop_loss"]
        tp = trade_params["take_profit"]

        record = None
        order = None
        base_balance_before = 0.0
        filled_size = 0.0
        market = None
        try:
            self.exchange.load_markets()
            market = self.exchange.market(symbol)
            size = float(self.exchange.amount_to_precision(symbol, size))
            sl = float(self.exchange.price_to_precision(symbol, sl))
            tp = float(self.exchange.price_to_precision(symbol, tp))
            entry_price = float(trade_params["entry"])
            if not math.isfinite(size) or size <= 0:
                raise ValueError(f"Order size must be positive and finite; got {size!r}.")
            if not all(math.isfinite(value) for value in (sl, tp, entry_price)):
                raise ValueError("Entry, stop-loss, and take-profit prices must be finite.")
            if not sl < entry_price < tp:
                raise ValueError("LONG live orders require stop_loss < entry < take_profit.")
            min_amount = (market.get("limits", {}).get("amount", {}) or {}).get("min")
            min_cost = (market.get("limits", {}).get("cost", {}) or {}).get("min")
            if min_amount and size < min_amount:
                raise ValueError(f"Order size {size} below exchange minimum amount {min_amount}.")
            if min_cost and size * trade_params["entry"] < min_cost:
                raise ValueError(f"Order notional {size * trade_params['entry']:.4f} below minimum {min_cost}.")

            base_asset = market.get("base") or symbol.split("/")[0]
            base_balance_before = self._get_free_asset_balance(base_asset)
            record = self._new_record(symbol, size, base_balance_before)
            self._update_record(record, "entry_submitting")
            try:
                order = self.exchange.create_market_order(
                    symbol,
                    "buy",
                    size,
                    params={"newClientOrderId": record["client_order_id"]},
                )
            except Exception as e:
                self._handle_ambiguous_entry_failure(
                    record, symbol, base_asset, base_balance_before, size, e
                )
                raise AssertionError("ambiguous entry handler must raise")

            reported_filled = order.get("filled")
            if reported_filled is None:
                reported_filled = order.get("amount")
            filled_size = float(reported_filled) if reported_filled is not None else 0.0
            if not math.isfinite(filled_size) or filled_size <= 0:
                raise ProtectionPlacementError(
                    f"Entry response contained invalid filled size {filled_size!r}."
                )
            fill_price = float(order.get("price") or order.get("average") or trade_params["entry"])
            self._update_record(
                record,
                "entry_filled",
                entry_order_id=self._order_id(order),
                filled_size=filled_size,
                fill_price=fill_price,
            )

            acquired_size = max(
                0.0, self._get_free_asset_balance(base_asset) - base_balance_before
            )
            protectable_size = float(
                self.exchange.amount_to_precision(
                    symbol, min(filled_size, acquired_size)
                )
            )
            if protectable_size <= 0:
                raise ProtectionPlacementError(
                    "The filled entry produced no protectable free base-asset balance."
                )

            logger.success(f"LIVE ORDER placed: {direction} {size} {symbol} @ {fill_price}")
            self._update_record(record, "protection_pending")
            try:
                protection = self._place_protection(
                    symbol,
                    market,
                    protectable_size,
                    sl,
                    tp,
                    record["client_order_id"],
                )
            except ProtectionPlacementError as e:
                self._compensate_entry(
                    record,
                    symbol,
                    base_asset,
                    base_balance_before,
                    filled_size,
                    created_orders=e.created_orders,
                )
                raise self._block(
                    f"{e}. The entry was compensated; live trading is stopped for review."
                ) from e

            self._update_record(
                record,
                "protected",
                protection_mode=protection["mode"],
                protected_size=protectable_size,
                order_list_id=protection["order_list_id"],
                tp_order_id=protection["tp_order_id"],
                sl_order_id=protection["sl_order_id"],
            )

            trade = {
                "timestamp": _utc_now().isoformat(),
                "symbol": symbol,
                "direction": direction,
                "entry_price": fill_price,
                "stop_loss": sl,
                "take_profit": tp,
                "size": protectable_size,
                "risk_amount": trade_params["risk_amount"],
                "rr_ratio": trade_params["rr_ratio"],
                "confidence": confidence,
                "entry_order_id": self._order_id(order),
                "tp_order_id": protection["tp_order_id"],
                "sl_order_id": protection["sl_order_id"],
                "mode": "live",
                "status": "open",
                "notes": f"protection={protection['mode']}; order_list_id={protection['order_list_id']}",
            }

            self.open_orders.append(trade)
            self.session_trade_count += 1
            logger.log_trade(trade)
            logger.trade_signal(
                direction, symbol, fill_price, sl, tp, protectable_size, confidence
            )
            return trade

        except LiveSafetyError:
            if (
                record is not None
                and order is not None
                and market is not None
                and record.get("status") not in {"protected", "compensated", "manual_reconciliation_required"}
            ):
                base_asset = market.get("base") or symbol.split("/")[0]
                self._compensate_entry(
                    record,
                    symbol,
                    base_asset,
                    base_balance_before,
                    filled_size or size,
                )
            raise
        except ProtectionPlacementError as e:
            if record is not None and order is not None and market is not None:
                base_asset = market.get("base") or symbol.split("/")[0]
                self._compensate_entry(
                    record,
                    symbol,
                    base_asset,
                    base_balance_before,
                    filled_size or size,
                )
            raise self._block(f"Live entry could not be safely protected: {e}") from e
        except Exception as e:
            if record is not None and order is not None and market is not None:
                base_asset = market.get("base") or symbol.split("/")[0]
                self._compensate_entry(
                    record,
                    symbol,
                    base_asset,
                    base_balance_before,
                    filled_size or size,
                )
                raise self._block(
                    f"Unexpected post-entry failure was compensated: {e}"
                ) from e
            logger.error(f"Live order validation failed before submission: {e}")
            return None

    def session_stats(self) -> dict:
        """Return limited live-session stats without pretending to calculate fills/PnL."""
        balance = self.get_balance()
        return {
            "trades": self.session_trade_count,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "pnl": 0.0,
            "balance": balance,
        }


def create_executor(mode: str | None = None, initial_balance: float | None = None):
    """Return the appropriate trader for the current mode."""
    mode = mode or config.MODE
    if mode == "live":
        logger.warning("LIVE trading mode - real orders will be placed!")
        return LiveTrader()
    return PaperTrader(initial_balance)
