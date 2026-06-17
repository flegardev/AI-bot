"""
main.py — Entry point for the trading bot.

Usage:
  python main.py                   # Uses MODE from .env (default: paper)
  python main.py --mode backtest   # Run a backtest
  python main.py --mode paper      # Paper trading (real-time, no real orders)
  python main.py --mode live       # LIVE trading — real money!
  python main.py --symbol ETH/USDT # Trade a different symbol
"""
import argparse
import time
import sys
import io
import signal
from datetime import datetime, timezone

# Force UTF-8 output so box/color characters don't crash on Windows cp1252
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import config
import logger
from data.fetcher     import fetch_all_timeframes
from strategy.bias    import get_bias_details
from strategy.liquidity import detect_liquidity_sweep
from strategy.entry   import get_entry_details
from risk.manager     import build_trade
from execution.executor import create_executor
from ml.predictor     import get_predictor, MLPredictor


# ─── Argument Parsing ─────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="Crypto Trading Bot")
    parser.add_argument("--mode",   choices=["backtest", "paper", "live"],
                        default=config.MODE)
    parser.add_argument("--symbol", default=config.SYMBOL)
    parser.add_argument("--no-ml",  action="store_true",
                        help="Disable ML confidence filter")
    parser.add_argument("--train-ml", action="store_true",
                        help="Train/update the ML model after a backtest")
    return parser.parse_args()


# ─── Strategy Evaluation (shared by paper & live) ─────────────────────────────

def evaluate_signal(symbol: str, use_ml: bool = True, account_balance: float | None = None) -> dict | None:
    """
    Run the full 3-layer strategy check on fresh data.

    Returns a trade-ready dict (from risk.manager) or None if no signal.
    """
    # 1. Fetch all three timeframes
    try:
        data = fetch_all_timeframes(symbol)
    except Exception as e:
        logger.error(f"Data fetch failed: {e}")
        return None

    df_1h  = data[config.TF_BIAS]
    df_15m = data[config.TF_LIQUIDITY]
    df_5m  = data[config.TF_ENTRY]

    # 2. Bias check (1H EMA 200)
    bias = get_bias_details(df_1h)
    if bias["bias"] is None:
        logger.info("No bias — not enough 1H data yet.")
        return None
    logger.info(f"Bias: {bias['bias']}  |  Price: {bias['price']:.2f}  EMA200: {bias['ema_200']:.2f}  "
                f"Dist: {bias['ema_distance_pct']:+.2%}")

    # 3. Liquidity sweep check (15m)
    sweep = detect_liquidity_sweep(df_15m)
    if not sweep["swept"]:
        logger.info("No liquidity sweep on 15m.")
        return None

    # Sweep must agree with bias
    if bias["bias"] == "LONG"  and sweep["direction"] != "bullish":
        logger.info(f"Sweep direction ({sweep['direction']}) conflicts with LONG bias — skip.")
        return None
    if bias["bias"] == "SHORT" and sweep["direction"] != "bearish":
        logger.info(f"Sweep direction ({sweep['direction']}) conflicts with SHORT bias — skip.")
        return None

    logger.info(f"Liquidity sweep detected: {sweep['direction'].upper()}  "
                f"level={sweep['level']:.4f}")

    # 4. Entry confirmation (5m CHoCH / BOS)
    entry_det = get_entry_details(df_5m, sweep["direction"])
    if not entry_det["signal"]:
        logger.info(f"No CHoCH / BOS on 5m yet — waiting ...")
        return None

    logger.info(f"Entry confirmed: {entry_det['type']}  "
                f"RSI={entry_det['rsi_14']:.1f}  ATR={entry_det['atr_14']:.4f}")

    # 5. Build trade parameters
    direction = "LONG" if sweep["direction"] == "bullish" else "SHORT"
    atr       = entry_det["atr_14"]
    price     = entry_det["current_price"]

    trade_params = build_trade(
        direction       = direction,
        entry_price     = price,
        liquidity_level = sweep["level"],
        atr             = atr,
        account_balance = account_balance or config.ACCOUNT_BALANCE,
    )

    if trade_params is None:
        return None   # Didn't meet R:R requirements

    # 6. ML confidence filter
    confidence = None
    if use_ml and config.USE_ML_FILTER:
        predictor = get_predictor()
        features  = MLPredictor.extract_features(bias, entry_det, sweep["direction"], df_5m)
        take_trade, confidence = predictor.is_confident(features)

        if not take_trade:
            logger.warning(
                f"ML filter blocked trade — confidence {confidence:.1%} < "
                f"threshold {config.ML_CONFIDENCE_THRESHOLD:.0%}"
            )
            return None
        logger.success(f"ML filter passed — confidence: {confidence:.1%}")

    trade_params["confidence"] = confidence
    return trade_params


# ─── Main Loop: Paper / Live ──────────────────────────────────────────────────

def run_trading_loop(mode: str, symbol: str, use_ml: bool = True):
    """
    Main event loop for paper and live trading.
    Polls for new 5m candles and runs the strategy on each.
    """
    executor = create_executor(mode)
    logger.info(f"Bot started — mode={mode.upper()}  symbol={symbol}")
    logger.info(f"ML filter: {'ON' if use_ml else 'OFF'}  "
                f"Max trades/session: {config.MAX_TRADES_PER_SESSION}")
    print()

    POLL_SECONDS = 60  # Check every 60 seconds (1 minute)
    last_candle_ts = None

    try:
        while True:
            now = datetime.now(timezone.utc)

            # Update any open paper positions with current price
            if mode == "paper" and executor.positions:
                try:
                    from data.fetcher import fetch_ohlcv
                    latest = fetch_ohlcv(symbol, "1m", limit=2)
                    current_price = latest.iloc[-1]["close"]
                    executor.update_positions(current_price)
                except Exception as e:
                    logger.error(f"Failed to update positions: {e}")

            # Only evaluate on a new 5m candle (avoid duplicate signals)
            current_minute = now.minute
            if current_minute % 5 == 0 and now.second < POLL_SECONDS:
                candle_key = f"{now.hour}:{(now.minute // 5) * 5}"
                if candle_key != last_candle_ts:
                    last_candle_ts = candle_key
                    logger.info(f"--- Checking {symbol} @ {now.strftime('%H:%M:%S')} UTC ---")

                    if executor.can_trade():
                        try:
                            account_balance = executor.get_balance()
                        except Exception as e:
                            logger.error(f"Could not fetch account balance: {e}")
                            account_balance = config.ACCOUNT_BALANCE
                        signal = evaluate_signal(symbol, use_ml=use_ml, account_balance=account_balance)
                        if signal:
                            executor.open_position(signal, symbol, signal.get("confidence"))
                    else:
                        logger.warning(f"Session trade cap reached ({config.MAX_TRADES_PER_SESSION}). "
                                       "Waiting for next session.")

            time.sleep(POLL_SECONDS)

    except KeyboardInterrupt:
        print()
        logger.info("Bot stopped by user.")
        stats = executor.session_stats()
        logger.session_summary(
            stats["balance"], stats["trades"], stats["wins"], stats["pnl"]
        )
        sys.exit(0)


# ─── Entry Point ──────────────────────────────────────────────────────────────

def _raise_keyboard_interrupt(_signum, _frame):
    """SIGTERM handler: re-route to the loop's graceful KeyboardInterrupt path."""
    raise KeyboardInterrupt


def main():
    args = parse_args()
    use_ml = not args.no_ml

    print()
    print("  +==========================================+")
    print("  |      CRYPTO TRADING BOT  v1.0            |")
    print("  |  1H Bias + 15m Liquidity + 5m CHoCH/BOS  |")
    print("  +==========================================+")
    print()

    if args.mode == "backtest":
        logger.info("Starting BACKTEST mode ...")
        from backtest.engine import BacktestEngine
        engine = BacktestEngine(symbol=args.symbol)
        train_ml = args.train_ml or config.TRAIN_ML_AFTER_BACKTEST
        results = engine.run(train_ml=train_ml)
        if not train_ml:
            logger.info("ML training skipped. Use --train-ml to update the model from this backtest.")

    elif args.mode in ("paper", "live"):
        if args.mode == "live":
            print("  ⚠  WARNING: LIVE MODE — real orders will be placed!")
            confirm = input("  Type 'YES' to continue: ").strip()
            if confirm != "YES":
                print("  Aborted.")
                sys.exit(0)
        # Treat SIGTERM (e.g. `docker stop` / orchestrator shutdown) like Ctrl-C so
        # the trading loop unwinds through its graceful KeyboardInterrupt path
        # (session summary, exit 0) instead of being terminated abruptly.
        signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
        run_trading_loop(args.mode, args.symbol, use_ml=use_ml)

    else:
        logger.error(f"Unknown mode: {args.mode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
