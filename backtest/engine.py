"""
backtest/engine.py — Multi-timeframe backtesting engine.

How it works:
  1. Download full history for 1H, 15m, and 5m.
  2. Walk forward through every 5m bar (as if it were live).
  3. At each bar, look up the corresponding 1H and 15m data
     (only candles that would have been visible at that moment in time).
  4. Run bias → liquidity sweep → entry signal checks.
  5. If a signal fires, simulate a trade (SL / TP fill on future 5m bars).
  6. Collect all trades, compute statistics, optionally train the ML model.

Performance metrics computed:
  Total trades, Win rate, Total PnL, Max drawdown, Profit factor,
  Sharpe ratio (simplified), Average R:R
"""
import pandas as pd
import numpy as np
from datetime import datetime

import config
import logger
from data.fetcher import fetch_historical_range
from strategy.bias     import get_bias_details
from strategy.liquidity import detect_liquidity_sweep
from strategy.entry    import get_entry_details
from risk.manager      import build_trade


# ─── Helper: align timestamps across timeframes ───────────────────────────────

def _get_slice(df: pd.DataFrame, as_of: pd.Timestamp, min_bars: int = 10) -> pd.DataFrame | None:
    """
    Return all rows of `df` up to and including the bar that covers `as_of`.
    Returns None if fewer than min_bars are available.
    """
    mask = df.index <= as_of
    sliced = df[mask]
    if len(sliced) < min_bars:
        return None
    return sliced


# ─── Main Engine ─────────────────────────────────────────────────────────────

class BacktestEngine:

    def __init__(self, symbol: str = None, balance: float = None):
        self.symbol  = symbol  or config.SYMBOL
        self.balance = balance or config.INITIAL_BALANCE
        self.peak_balance = self.balance
        self.trades: list[dict] = []
        self.open_trade: dict | None = None  # Max 1 open at a time for simplicity

    # ── Data Download ─────────────────────────────────────────────────────────

    def _download_data(self) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        df_1h  = fetch_historical_range(self.symbol, config.TF_BIAS,      config.BACKTEST_START, config.BACKTEST_END)
        df_15m = fetch_historical_range(self.symbol, config.TF_LIQUIDITY, config.BACKTEST_START, config.BACKTEST_END)
        df_5m  = fetch_historical_range(self.symbol, config.TF_ENTRY,     config.BACKTEST_START, config.BACKTEST_END)
        return df_1h, df_15m, df_5m

    # ── Trade Simulation ──────────────────────────────────────────────────────

    def _simulate_trade_exit(
        self,
        trade:    dict,
        df_5m:    pd.DataFrame,
        from_idx: int,
    ) -> dict:
        """
        Walk forward from `from_idx` in the 5m dataframe to find SL or TP hit.
        Returns the closed trade record.
        """
        direction = trade["direction"]
        sl = trade["stop_loss"]
        tp = trade["take_profit"]

        for i in range(from_idx, len(df_5m)):
            bar = df_5m.iloc[i]
            if direction == "LONG":
                if bar["low"] <= sl:
                    return self._close_trade(trade, sl, "loss", df_5m.index[i])
                if bar["high"] >= tp:
                    return self._close_trade(trade, tp, "win",  df_5m.index[i])
            else:  # SHORT
                if bar["high"] >= sl:
                    return self._close_trade(trade, sl, "loss", df_5m.index[i])
                if bar["low"] <= tp:
                    return self._close_trade(trade, tp, "win",  df_5m.index[i])

        # End of data reached without exit — close at last price
        last_price = df_5m.iloc[-1]["close"]
        return self._close_trade(trade, last_price, "open", df_5m.index[-1])

    def _close_trade(self, trade: dict, exit_price: float, result: str, ts: pd.Timestamp) -> dict:
        entry = trade["entry"]
        size  = trade["size"]
        raw_exit_price = exit_price

        # Conservative fill modeling: exits are slipped against the position
        # and fees are charged on both entry and exit notional.
        if trade["direction"] == "LONG":
            exit_price = exit_price * (1 - config.BACKTEST_SLIPPAGE_PCT)
        else:
            exit_price = exit_price * (1 + config.BACKTEST_SLIPPAGE_PCT)

        if trade["direction"] == "LONG":
            pnl = (exit_price - entry) * size
        else:
            pnl = (entry - exit_price) * size

        fees = (entry * size + exit_price * size) * config.BACKTEST_FEE_RATE
        pnl -= fees

        pnl_pct = pnl / self.balance * 100
        self.balance += pnl
        self.peak_balance = max(self.peak_balance, self.balance)

        return {
            **trade,
            "exit_price":    round(exit_price, 6),
            "raw_exit_price": round(raw_exit_price, 6),
            "fees":          round(fees, 4),
            "exit_timestamp": ts.isoformat(),
            "pnl":           round(pnl, 4),
            "pnl_pct":       round(pnl_pct, 4),
            "result":        result,
            "balance_after": round(self.balance, 2),
        }

    # ── Core Loop ─────────────────────────────────────────────────────────────

    def run(self, train_ml: bool = True) -> dict:
        """
        Run the full backtest.

        Args:
            train_ml: if True, train the ML model on the resulting trades

        Returns:
            dict with all trades and performance statistics
        """
        logger.info(f"Backtest: {self.symbol}  {config.BACKTEST_START} → {config.BACKTEST_END}")
        df_1h, df_15m, df_5m = self._download_data()

        if df_5m.empty:
            logger.error("No 5m data downloaded — check your dates and API credentials.")
            return {}

        session_trades = 0  # Reset session counter each day
        current_day    = None
        trade_idx      = 0  # 5m bar index to start scanning after opening a trade

        logger.info(f"Walking {len(df_5m)} 5m bars ...")

        for i in range(config.STRUCTURE_LOOKBACK, len(df_5m)):
            ts = df_5m.index[i]

            # Reset daily trade counter
            day = ts.date()
            if day != current_day:
                current_day    = day
                session_trades = 0

            if session_trades >= config.MAX_TRADES_PER_SESSION:
                continue   # Max trades for the day

            # Skip bars that are still "inside" an open trade's projection
            if i < trade_idx:
                continue

            # ── Get aligned slices (only past data) ───────────────────────────
            slice_1h  = _get_slice(df_1h,  ts, min_bars=config.EMA_PERIOD)
            slice_15m = _get_slice(df_15m, ts, min_bars=config.LIQUIDITY_LOOKBACK)
            slice_5m  = _get_slice(df_5m,  ts, min_bars=config.STRUCTURE_LOOKBACK)

            if slice_1h is None or slice_15m is None or slice_5m is None:
                continue

            # ── Step 1: 1H Bias ───────────────────────────────────────────────
            bias = get_bias_details(slice_1h)
            if bias["bias"] is None:
                continue

            # ── Step 2: 15m Liquidity Sweep ───────────────────────────────────
            sweep = detect_liquidity_sweep(slice_15m)
            if not sweep["swept"]:
                continue

            # Sweep must agree with the bias
            if bias["bias"] == "LONG"  and sweep["direction"] != "bullish":
                continue
            if bias["bias"] == "SHORT" and sweep["direction"] != "bearish":
                continue

            # ── Step 3: 5m CHoCH / BOS ────────────────────────────────────────
            entry_det = get_entry_details(slice_5m, sweep["direction"])
            if not entry_det["signal"]:
                continue

            # ── Step 4: Build trade ───────────────────────────────────────────
            current_price = slice_5m.iloc[-1]["close"]
            atr           = entry_det.get("atr_14", current_price * 0.005)
            direction     = "LONG" if sweep["direction"] == "bullish" else "SHORT"
            entry_price   = (
                current_price * (1 + config.BACKTEST_SLIPPAGE_PCT)
                if direction == "LONG"
                else current_price * (1 - config.BACKTEST_SLIPPAGE_PCT)
            )

            trade_params = build_trade(
                direction       = direction,
                entry_price     = entry_price,
                liquidity_level = sweep["level"],
                atr             = atr,
                account_balance = self.balance,
            )

            if trade_params is None:
                continue

            # ── Step 5: Simulate exit ─────────────────────────────────────────
            # Capture the same features the live predictor sees at inference
            # time, so ML training data matches serving data (no train/serve
            # skew). volume_ratio mirrors MLPredictor.extract_features.
            vol_avg = slice_5m["volume"].rolling(20).mean().iloc[-1]
            volume_ratio = (
                slice_5m["volume"].iloc[-1] / vol_avg
                if pd.notna(vol_avg) and vol_avg > 0
                else 1.0
            )

            trade_params["entry_timestamp"] = ts.isoformat()
            trade_params["symbol"]          = self.symbol
            trade_params["mode"]            = "backtest"
            trade_params["sweep_type"]      = sweep["direction"]
            trade_params["signal_type"]     = entry_det.get("type", "BOS")
            trade_params["rsi_14"]          = entry_det.get("rsi_14", 50.0)
            trade_params["atr_pct"]         = entry_det.get("atr_pct", 0.0)
            trade_params["volume_ratio"]    = float(volume_ratio)
            trade_params["ema_dist_pct"]    = bias.get("ema_distance_pct", 0.0)

            closed = self._simulate_trade_exit(trade_params, df_5m, i + 1)
            self.trades.append(closed)
            session_trades += 1

            # Find the 5m index of the exit so we don't re-enter in the same region
            exit_ts = pd.Timestamp(closed["exit_timestamp"])
            if exit_ts.tzinfo is None:
                exit_ts = exit_ts.tz_localize("UTC")
            trade_idx = df_5m.index.searchsorted(exit_ts)

            result_emoji = "✓" if closed["result"] == "win" else "✗"
            logger.info(
                f"  [{ts.strftime('%Y-%m-%d %H:%M')}] "
                f"{trade_params['direction']} {trade_params['signal_type']}  "
                f"PnL=${closed['pnl']:+.2f}  "
                f"Balance=${closed['balance_after']:,.2f}  {result_emoji}"
            )

        stats = self._compute_stats()

        if train_ml and self.trades:
            self._train_ml()

        return {"trades": self.trades, "stats": stats}

    # ── Statistics ────────────────────────────────────────────────────────────

    def _compute_stats(self) -> dict:
        trades = self.trades
        if not trades:
            return {"message": "No trades generated."}

        wins   = [t for t in trades if t["result"] == "win"]
        losses = [t for t in trades if t["result"] == "loss"]
        pnls   = [t["pnl"] for t in trades]

        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss   = abs(sum(p for p in pnls if p < 0))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

        # Max drawdown
        running_balance = config.INITIAL_BALANCE
        peak = running_balance
        max_dd = 0.0
        for t in trades:
            running_balance += t["pnl"]
            peak = max(peak, running_balance)
            dd = (peak - running_balance) / peak
            max_dd = max(max_dd, dd)

        stats = {
            "total_trades":   len(trades),
            "wins":           len(wins),
            "losses":         len(losses),
            "win_rate":       len(wins) / len(trades),
            "total_pnl":      round(sum(pnls), 2),
            "gross_profit":   round(gross_profit, 2),
            "gross_loss":     round(gross_loss, 2),
            "profit_factor":  round(profit_factor, 3),
            "max_drawdown":   round(max_dd * 100, 2),
            "avg_rr":         round(np.mean([t["rr_ratio"] for t in trades]), 2),
            "final_balance":  round(self.balance, 2),
            "return_pct":     round((self.balance - config.INITIAL_BALANCE) / config.INITIAL_BALANCE * 100, 2),
        }

        print()
        logger.success("-" * 50)
        logger.success("  BACKTEST RESULTS")
        logger.success("-" * 50)
        for k, v in stats.items():
            print(f"  {k:<18}: {v}")
        print()

        return stats

    def _train_ml(self):
        """Use the backtest trade results to train the ML model."""
        from ml.predictor import get_predictor

        # Build feature records from backtest trades
        records = []
        for t in self.trades:
            if t["result"] not in ("win", "loss"):
                continue
            records.append({
                "ema_distance_pct": t.get("ema_dist_pct", 0.0),
                "rsi_14":           t.get("rsi_14", 50.0),
                "volume_ratio":     t.get("volume_ratio", 1.0),
                "atr_pct":          t.get("atr_pct", 0.0),
                "sweep_direction":  1 if t.get("sweep_type") == "bullish" else -1,
                "signal_type":      1 if t.get("signal_type") == "CHoCH" else 0,
                "result":           t["result"],
            })

        if records:
            predictor = get_predictor()
            predictor.train(records)
