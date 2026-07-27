"""Production CLI. Real-money mode is disabled unless three independent gates pass."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

import config
import logger
from backtest.engine import BacktestEngine
from brokers.binance import BinanceBrokerAdapter
from brokers.mt5 import MT5BrokerAdapter
from brokers.paper import PaperBrokerAdapter
from core.models import PositionSizeRequest
from core.strategy_engine import StrategyEngine
from execution.reconciliation import ReconciliationService
from execution.service import SafeExecutionService
from persistence.store import TradingStore
from risk.engine import RiskEngine, RiskLimits


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="MT5-first safe trading bot")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("check", "backtest", "paper", "live"):
        command = commands.add_parser(name)
        command.add_argument("--broker", choices=["mt5", "binance"], default=config.SETTINGS.broker)
        command.add_argument("--symbol", default=config.SYMBOL)
    commands.choices["backtest"].add_argument("--start", default=config.BACKTEST_START)
    commands.choices["backtest"].add_argument("--end", default=config.BACKTEST_END)
    commands.choices["backtest"].add_argument("--train-ml", action="store_true")
    commands.choices["paper"].add_argument("--once", action="store_true", help="Evaluate one completed-candle cycle")
    commands.choices["live"].add_argument("--once", action="store_true", help="Evaluate one completed-candle cycle")
    commands.add_parser("dashboard").add_argument("--port", type=int, default=8000)
    kill = commands.add_parser("kill-switch")
    kill.add_argument("--cancel-pending", action="store_true")
    commands.add_parser("reset-kill-switch")
    return root


def make_broker(name: str):
    if name == "mt5":
        return MT5BrokerAdapter()
    return BinanceBrokerAdapter(sandbox=config.USE_TESTNET)


def check(args: argparse.Namespace) -> int:
    checks: dict[str, object] = {}
    store = TradingStore(config.DATABASE_PATH)
    checks["database_writable"] = store.record_event("dry_run_check", {}, mode="check") > 0
    offset = datetime.now(timezone.utc).utcoffset()
    checks["clock_utc"] = offset is not None and offset.total_seconds() == 0
    try:
        broker = make_broker(args.broker)
        if args.broker == "mt5":
            checks.update(broker.check_connection(args.symbol))
        else:
            broker.connect()
            checks["account"] = broker.get_account_state().__dict__
            checks["symbol"] = broker.get_symbol_info(args.symbol).__dict__
            checks["tick"] = not broker.get_candles(args.symbol, "5m").empty
            checks["ok"] = True
        info = broker.get_symbol_info(args.symbol)
        checks["valid_lot_constraints"] = (
            info.volume_min > 0 and info.volume_step > 0 and info.volume_max >= info.volume_min
        )
        candles = broker.get_candles(args.symbol, "1h")
        checks["sufficient_history"] = len(candles) >= config.EMA_PERIOD
        broker.disconnect()
    except Exception as exc:
        checks["ok"] = False
        checks["error"] = str(exc)
    print(json.dumps(checks, indent=2, default=str))
    return 0 if checks.get("ok") else 1


def run_cycle(args: argparse.Namespace, live: bool) -> int:
    if live:
        config.SETTINGS.assert_live_allowed()
    market = make_broker(args.broker)
    market.connect()
    store = TradingStore(config.DATABASE_PATH)
    execution_broker = market if live else PaperBrokerAdapter(store, config.INITIAL_BALANCE, market.get_symbol_info(args.symbol))
    if not live:
        execution_broker.connect()
    reconciliation = ReconciliationService(execution_broker, store)
    reconciliation.run()
    frames = {
        tf: market.get_candles(args.symbol, tf)
        for tf in (config.TF_BIAS, config.TF_LIQUIDITY, config.TF_ENTRY)
    }
    as_of = datetime.now(timezone.utc)
    signal = StrategyEngine().evaluate(
        args.symbol, frames, as_of,
        can_long=execution_broker.capabilities.long,
        can_short=execution_broker.capabilities.short,
    )
    if not signal:
        logger.info("No signal on the latest completed candles.")
        execution_broker.disconnect()
        if market is not execution_broker:
            market.disconnect()
        return 0
    account = execution_broker.get_account_state()
    info = execution_broker.get_symbol_info(args.symbol)
    sizing_request = PositionSizeRequest(
        signal.symbol, signal.direction, signal.entry, signal.stop_loss,
        config.RISK_PER_TRADE, account, info
    )
    sizing = execution_broker.calculate_position_size(sizing_request)
    limits = RiskLimits(
        config.RISK_PER_TRADE, config.MAX_DAILY_LOSS_PCT, config.MAX_DRAWDOWN_PCT,
        config.MAX_OPEN_POSITIONS, config.SETTINGS.max_portfolio_risk_pct,
        config.SETTINGS.max_symbol_exposure_pct, config.SETTINGS.max_spread,
        config.SETTINGS.max_slippage, config.SETTINGS.stale_price_seconds,
    )
    high_water = float(store.get_state("equity_high_water", account.equity))
    high_water = max(high_water, account.equity)
    store.set_state("equity_high_water", high_water)
    start_day = float(store.get_state(f"start_equity_{as_of.date()}", account.equity))
    store.set_state(f"start_equity_{as_of.date()}", start_day)
    decision = RiskEngine(limits).evaluate(
        signal, sizing_request, execution_broker.get_open_positions(),
        equity_high_water=high_water, start_of_day_equity=start_day,
        live_entries_enabled=True, sizing=sizing
    )
    result = SafeExecutionService(execution_broker, store).execute(signal, decision)
    print(json.dumps(result.__dict__ if result else {"blocked": decision.reasons}, indent=2, default=str))
    execution_broker.disconnect()
    if market is not execution_broker:
        market.disconnect()
    return 0


def run_loop(args: argparse.Namespace, live: bool) -> int:
    if args.once:
        return run_cycle(args, live)
    logger.info("Continuous mode started; evaluating completed candles every 60 seconds.")
    try:
        while True:
            result = run_cycle(args, live)
            if result:
                return result
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Trading loop stopped by user.")
        return 0


def main() -> int:
    args = parser().parse_args()
    if args.command == "check":
        return check(args)
    if args.command == "backtest":
        config.BACKTEST_START, config.BACKTEST_END = args.start, args.end
        data_broker = make_broker(args.broker)
        data_broker.connect()
        try:
            start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
            end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
            frames = {
                timeframe: data_broker.get_candles(args.symbol, timeframe, start, end)
                for timeframe in (config.TF_BIAS, config.TF_LIQUIDITY, config.TF_ENTRY)
            }
            symbol_info = data_broker.get_symbol_info(args.symbol)
        finally:
            data_broker.disconnect()
        result = BacktestEngine(
            args.symbol, capabilities=data_broker.capabilities,
            symbol_info=symbol_info,
        ).run(train_ml=args.train_ml, data=frames)
        print(json.dumps(result["stats"], indent=2, default=str))
        return 0
    if args.command == "paper":
        return run_loop(args, live=False)
    if args.command == "live":
        try:
            return run_loop(args, live=True)
        except RuntimeError as exc:
            logger.error(str(exc))
            return 2
    if args.command == "dashboard":
        import web_demo
        web_demo.PORT = args.port
        web_demo.main()
        return 0
    service = SafeExecutionService(PaperBrokerAdapter(TradingStore(config.DATABASE_PATH)), TradingStore(config.DATABASE_PATH))
    if args.command == "kill-switch":
        service.kill_switch(args.cancel_pending)
    else:
        service.reset_kill_switch()
    return 0


if __name__ == "__main__":
    sys.exit(main())
