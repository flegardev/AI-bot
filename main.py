"""Research and demo CLI. Real-money trading still requires every explicit opt-in."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import config
import logger
from backtest.demo import export_demo_report
from backtest.engine import BacktestEngine
from brokers.base import BrokerCapabilities
from brokers.binance import BinanceBrokerAdapter
from brokers.mt5 import MT5BrokerAdapter
from brokers.paper import PaperBrokerAdapter
from core.models import Direction, PositionSizeRequest, SymbolInfo
from core.strategy_engine import StrategyEngine
from core.timeframes import filter_closed_candles, timeframe_to_timedelta
from data.contracts import MarketDataContract
from data.quality import DataQualityError, inspect_dataset, inspect_frame, warmup_start
from execution.reconciliation import ReconciliationService
from execution.service import SafeExecutionService
from ml.predictor import MLPredictor
from persistence.store import TradingStore
from risk.engine import RiskEngine, RiskLimits


def parser():
    root = argparse.ArgumentParser(description="MT5 trading research and guarded execution")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("check", "backtest", "research", "compete", "cache-data", "paper", "live"):
        command = commands.add_parser(name)
        command.add_argument("--broker", choices=["mt5", "binance"], default=config.SETTINGS.broker)
        command.add_argument("--symbol", default=config.SYMBOL)
    for name in ("backtest", "research", "compete", "cache-data"):
        command = commands.choices[name]
        command.add_argument("--start", default=config.BACKTEST_START)
        command.add_argument("--end", default=config.BACKTEST_END)
        command.add_argument("--calendar", choices=["fx", "fx_ny", "24/7"], default=config.MARKET_CALENDAR)
        command.add_argument("--output", default=config.ARTIFACT_DIR)
        command.add_argument("--data-dir", help="Offline UTC OHLCV CSVs (1h/15m/5m.csv) and symbol.json")
    commands.choices["backtest"].add_argument("--train-ml", action="store_true")
    commands.choices["research"].add_argument("--folds", type=int, default=3)
    commands.choices["compete"].add_argument("--budget", type=int, default=48)
    for name in ("paper", "live"):
        commands.choices[name].add_argument("--once", action="store_true")
    commands.add_parser("dashboard").add_argument("--port", type=int, default=8000)
    commands.add_parser("kill-switch").add_argument("--cancel-pending", action="store_true")
    commands.add_parser("reset-kill-switch")
    for name in ("kill-switch", "reset-kill-switch"):
        commands.choices[name].add_argument("--mode", choices=["paper", "live"], default="paper")
    commands.add_parser("demo-report").add_argument("--output", default="artifacts/demo")
    commands.add_parser("diagnostic").add_argument("--output", default="artifacts/diagnostic")
    return root


def make_broker(name):
    return MT5BrokerAdapter() if name == "mt5" else BinanceBrokerAdapter(sandbox=config.USE_TESTNET)


def load_history(market, symbol, start, end):
    if end <= start:
        raise ValueError("end must be after start")
    frames = {tf: market.get_candles(symbol, tf, warmup_start(start), end)
              for tf in (config.TF_BIAS, config.TF_LIQUIDITY, config.TF_ENTRY)}
    if isinstance(market, MT5BrokerAdapter):
        # MT5 Forex charts are bid bars. Replay expects midpoint bars.
        for frame in frames.values():
            frame.loc[:, ["open", "high", "low", "close"]] += config.BACKTEST_SPREAD / 2
            frame.attrs["data_contract"]["price_basis"] = "midpoint"
    return frames


def check(args):
    checks: dict[str, Any] = {"database_writable": TradingStore(config.DATABASE_PATH).record_event("dry_run_check", {}, mode="check") > 0}
    broker = make_broker(args.broker)
    try:
        broker.connect()
        quote = broker.get_quote(args.symbol)
        quote.validate(config.SETTINGS.stale_price_seconds, config.SETTINGS.max_spread)
        checks.update(quote={"bid": quote.bid, "ask": quote.ask, "timestamp": quote.timestamp.isoformat()}, quote_ready=True)
        info = broker.get_symbol_info(args.symbol)
        checks["valid_lot_constraints"] = info.volume_min > 0 and info.volume_step > 0 and info.volume_max >= info.volume_min
        checks["sufficient_history"] = len(broker.get_candles(args.symbol, config.TF_BIAS)) >= config.EMA_PERIOD
        checks["ok"] = all(checks.values())
    except Exception as exc:
        checks.update(ok=False, error=str(exc))
    finally:
        broker.disconnect()
    print(json.dumps(checks, indent=2, default=str))
    return 0 if checks["ok"] else 1


def replay_paper_exits(broker, frame, store, symbol):
    """Process each completed bar once; replay persists across process restarts."""
    key = f"paper_last_bar_{symbol}"
    frame = filter_closed_candles(frame, config.TF_ENTRY)
    last = store.get_state(key)
    step = timeframe_to_timedelta(config.TF_ENTRY)
    if not broker.get_open_positions(symbol):
        if not frame.empty:
            store.set_state(key, frame.index[-1].isoformat())
        return
    if frame.empty:
        current_open = pd.Timestamp.now(tz="UTC").floor("5min")
        if all(pd.Timestamp(position.opened_at) >= current_open for position in broker.get_open_positions(symbol)):
            return
        raise DataQualityError("paper exit history is unavailable")
    if last:
        bars = frame.loc[frame.index > pd.Timestamp(last)]
        positions = broker.get_open_positions(symbol)
        if positions and not bars.empty:
            coverage = inspect_frame(frame, config.TF_ENTRY, pd.Timestamp(last) + step,
                bars.index[-1] + step, config.MARKET_CALENDAR)
            if not coverage["ok"]:
                raise DataQualityError("paper exit replay has a data gap; reconcile before resuming")
    else:
        positions = broker.get_open_positions(symbol)
        if positions and (frame.empty or min(p.opened_at for p in positions) < frame.index[0]):
            raise DataQualityError("paper position predates available exit history")
        bars = frame
    for _opened, bar in bars.iterrows():
        broker.apply_completed_bar(symbol, bar, config.TF_ENTRY, datetime.now(timezone.utc))
        if not broker.get_open_positions(symbol):
            store.set_state(key, frame.index[-1].isoformat())
            break


def run_cycle(args, live):
    if live:
        config.SETTINGS.assert_live_allowed()
        if args.broker != "mt5":
            raise RuntimeError("Live execution is supported only on the guarded MT5 path")
    market = make_broker(args.broker)
    execution = None
    database = Path(config.DATABASE_PATH)
    # A paper idempotency response must never be reused for live execution.
    store = TradingStore(database.with_stem(database.stem + "-live") if live else database)
    if not live:
        settings = {"symbol": args.symbol, "broker": args.broker, "risk": config.RISK_PER_TRADE,
            "max_spread": config.SETTINGS.max_spread, "max_slippage": config.SETTINGS.max_slippage,
            "ema_period": config.EMA_PERIOD, "liquidity_lookback": config.LIQUIDITY_LOOKBACK,
            "structure_lookback": config.STRUCTURE_LOOKBACK, "min_rr": config.MIN_RISK_REWARD,
            "spread": config.BACKTEST_SPREAD, "fee": config.BACKTEST_FEE_RATE,
            "slippage_fraction": config.BACKTEST_SLIPPAGE_PCT, "ml_enabled": config.USE_ML_FILTER}
        model = Path(config.ML_MODEL_PATH)
        settings["model_sha256"] = hashlib.sha256(model.read_bytes()).hexdigest() if model.exists() else None
        source = Path(__file__).resolve().parent
        code = hashlib.sha256()
        for folder in ("backtest", "brokers", "core", "data", "execution", "ml", "risk", "strategy"):
            for path in sorted((source / folder).glob("*.py")):
                code.update(path.read_bytes())
        for filename in ("main.py", "config.py"):
            code.update((source / filename).read_bytes())
        settings["code_sha256"] = code.hexdigest()
        digest = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()
        store.record_event("demo_configuration", {"config_sha256": digest, "settings": settings}, mode="paper")
    try:
        market.connect()
        info = market.get_symbol_info(args.symbol)
        execution = market if live else PaperBrokerAdapter(store, config.INITIAL_BALANCE, info,
            spread=config.BACKTEST_SPREAD,
            commission_per_lot=config.BACKTEST_FEE_RATE * info.contract_size,
            slippage_pct=config.BACKTEST_SLIPPAGE_PCT,
            swap_per_lot_day=config.BACKTEST_SWAP_PER_LOT_DAY)
        if not live:
            execution.connect()
        ReconciliationService(execution, store).run()
        frames = {tf: market.get_candles(args.symbol, tf)
                  for tf in (config.TF_BIAS, config.TF_LIQUIDITY, config.TF_ENTRY)}
        if not live and execution.get_open_positions(args.symbol):
            cursor = store.get_state(f"paper_last_bar_{args.symbol}")
            first = frames[config.TF_ENTRY].index[0] if not frames[config.TF_ENTRY].empty else None
            if cursor and (first is None or pd.Timestamp(cursor) < first):
                frames[config.TF_ENTRY] = market.get_candles(args.symbol, config.TF_ENTRY,
                    pd.Timestamp(cursor).to_pydatetime(), datetime.now(timezone.utc))
        if isinstance(market, MT5BrokerAdapter):
            for frame in frames.values():
                frame.loc[:, ["open", "high", "low", "close"]] += config.BACKTEST_SPREAD / 2
        if not live:
            replay_paper_exits(execution, frames[config.TF_ENTRY], store, args.symbol)
        # Quotes are mandatory even when the rules would otherwise yield no signal.
        quote = market.get_quote(args.symbol)
        observation = {"bid": quote.bid, "ask": quote.ask, "timestamp": quote.timestamp.isoformat(),
            "received_at": datetime.now(timezone.utc).isoformat(), "valid": False}
        try:
            quote.validate(config.SETTINGS.stale_price_seconds, config.SETTINGS.max_spread)
            observation["valid"] = True
        finally:
            store.record_event("market_observation", observation, mode="live" if live else "paper", symbol=args.symbol)
        as_of = datetime.now(timezone.utc)
        if not live:
            execution.set_clock(as_of)
            execution.spread = quote.spread
            midpoint = (quote.ask + quote.bid) / 2
            execution.process_bar(args.symbol, pd.Series({"open": midpoint, "high": midpoint,
                "low": midpoint, "close": midpoint}, name=pd.Timestamp(as_of)), as_of)
        for tf, frame in frames.items():
            if frame.empty or (as_of - (frame.index[-1] + timeframe_to_timedelta(tf))).total_seconds() > timeframe_to_timedelta(tf).total_seconds() * 2:
                raise DataQualityError(f"stale or missing {tf} candles")
            report = inspect_frame(frame, tf, calendar=config.MARKET_CALENDAR)
            if not report["ok"]:
                raise DataQualityError(f"{tf} candle integrity: {report['issues']}")
        strategy = StrategyEngine()
        snapshot = execution.get_account_state()
        high_water = max(float(store.get_state("equity_high_water", snapshot.equity)), snapshot.equity)
        store.set_state("equity_high_water", high_water)
        day_key = f"start_equity_{as_of.date()}"
        day_equity = float(store.get_state(day_key, snapshot.equity))
        store.set_state(day_key, day_equity)
        evaluation = strategy.evaluate_result(args.symbol, frames, as_of,
            can_long=execution.capabilities.long, can_short=execution.capabilities.short)
        signal = evaluation.signal
        store.record_event("strategy_evaluation", evaluation.to_record(), mode="live" if live else "paper", symbol=args.symbol)
        if not signal:
            logger.info(f"No entry: {strategy.last_reason}")
            return 0
        original = signal.entry
        price = quote.ask if signal.direction == Direction.LONG else quote.bid
        if abs(price - original) > config.SETTINGS.max_slippage:
            raise ValueError("signal-to-market move exceeds slippage limit")
        if not live:
            direction = 1 if signal.direction == Direction.LONG else -1
            price += direction * execution._cost((quote.bid + quote.ask) / 2)
        signal = replace(signal, entry=price)
        account = execution.get_account_state()
        request = PositionSizeRequest(signal.symbol, signal.direction, price, signal.stop_loss,
            config.RISK_PER_TRADE, account, info,
            estimated_commission_per_lot=2 * config.BACKTEST_FEE_RATE * info.contract_size
            + config.SETTINGS.max_slippage / info.tick_size * info.tick_value)
        sizing = execution.calculate_position_size(request)
        positions = execution.get_open_positions()
        portfolio_risk = sum(abs(p.entry_price - p.stop_loss) / market.get_symbol_info(p.symbol).tick_size
                             * market.get_symbol_info(p.symbol).tick_value * p.volume for p in positions)
        symbol_notional = sum(p.entry_price * info.contract_size * p.volume for p in positions if p.symbol == signal.symbol)
        limits = RiskLimits(config.RISK_PER_TRADE, config.MAX_DAILY_LOSS_PCT, config.MAX_DRAWDOWN_PCT,
            config.MAX_OPEN_POSITIONS, config.SETTINGS.max_portfolio_risk_pct,
            config.SETTINGS.max_symbol_exposure_pct, config.SETTINGS.max_spread,
            config.SETTINGS.max_slippage, config.SETTINGS.stale_price_seconds)
        service = SafeExecutionService(execution, store)
        decision = RiskEngine(limits).evaluate(signal, request, positions,
            equity_high_water=high_water, start_of_day_equity=day_equity,
            spread=quote.spread, price_timestamp=quote.timestamp, portfolio_risk=portfolio_risk,
            symbol_notional=symbol_notional, live_entries_enabled=service.entries_enabled, sizing=sizing)
        result = service.execute(signal, decision, max_slippage=config.SETTINGS.max_slippage,
            reference_price=price if live else (quote.bid + quote.ask) / 2,
            execution_timestamp=as_of if live else quote.timestamp)
        print(json.dumps(result.__dict__ if result else {"blocked": decision.reasons}, indent=2, default=str))
        return 0
    except (ValueError, RuntimeError) as exc:
        store.record_event("cycle_blocked", {"error": str(exc)}, mode="live" if live else "paper", symbol=args.symbol)
        logger.error(f"Cycle blocked: {exc}")
        return 2
    finally:
        if execution is not None and execution is not market:
            store.record_event("paper_account_snapshot", execution.get_account_state().__dict__, mode="paper", symbol=args.symbol)
            execution.disconnect()
        market.disconnect()


def run_loop(args, live):
    if args.once:
        return run_cycle(args, live)
    logger.info("Evaluating every 60 seconds; data failures block new entries.")
    try:
        while True:
            result = run_cycle(args, live)
            if result:
                return result
            time.sleep(60)
    except KeyboardInterrupt:
        return 0


def main():
    args = parser().parse_args()
    if args.command == "diagnostic":
        from data.fixtures import diagnostic_fixture
        frames, info, start, end = diagnostic_fixture(Path(args.output) / "fixture")
        result = BacktestEngine(info.symbol, symbol_info=info, artifact_dir=Path(args.output) / "backtest").run(
            data=frames, start=start, end=end, strict_data=True, calendar="24/7")
        print(json.dumps({"dataset_kind": "synthetic", "stats": result["stats"], "artifacts": args.output}, indent=2))
        return 0
    if args.command == "check":
        return check(args)
    if args.command in {"backtest", "research", "compete", "cache-data"}:
        broker = None
        try:
            start_value, end_value = pd.Timestamp(args.start), pd.Timestamp(args.end)
            start = (start_value.tz_localize("UTC") if start_value.tzinfo is None else start_value.tz_convert("UTC")).to_pydatetime()
            end = (end_value.tz_localize("UTC") if end_value.tzinfo is None else end_value.tz_convert("UTC")).to_pydatetime()
            if args.data_dir:
                directory = Path(args.data_dir)
                frames = {tf: pd.read_csv(directory / f"{tf}.csv", index_col="timestamp")
                          for tf in (config.TF_BIAS, config.TF_LIQUIDITY, config.TF_ENTRY)}
                contract_path = directory / "contract.json"
                contract = MarketDataContract.read(contract_path) if contract_path.exists() else None
                for tf, frame in frames.items():
                    if contract:
                        frames[tf] = contract.normalize(frame, config.BACKTEST_SPREAD)
                        if args.calendar != contract.calendar:
                            raise ValueError("--calendar disagrees with cached data contract")
                    else:
                        frame.index = pd.DatetimeIndex(pd.to_datetime(frame.index, utc=True))
                info = SymbolInfo(**json.loads((directory / "symbol.json").read_text(encoding="utf-8")))
                if info.symbol != args.symbol:
                    raise ValueError("symbol.json does not match --symbol")
                capabilities = BrokerCapabilities(long=info.can_long, short=info.can_short)
            else:
                broker = make_broker(args.broker)
                broker.connect()
                frames = load_history(broker, args.symbol, start, end)
                info = broker.get_symbol_info(args.symbol)
                capabilities = broker.capabilities
            if args.command == "cache-data":
                target = Path(args.output)
                target.mkdir(parents=True, exist_ok=True)
                for tf, frame in frames.items():
                    frame.to_csv(target / f"{tf}.csv", index_label="timestamp")
                contract = MarketDataContract(f"{args.broker}:{args.symbol}", calendar=args.calendar)
                (target / "contract.json").write_text(json.dumps(asdict(contract), indent=2), encoding="utf-8")
                (target / "symbol.json").write_text(json.dumps(asdict(info), indent=2), encoding="utf-8")
                report = inspect_dataset(frames, start, end, args.calendar, True)
                (target / "data_quality.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(json.dumps(report, indent=2))
                return 0 if report["ok"] else 2
            if args.command == "compete":
                from backtest.competition import competition
                result = competition(frames, args.symbol, info, start, end, args.output, args.calendar,
                                     max_experiments=args.budget)
                print(json.dumps(result, indent=2, default=str))
            elif args.command == "research":
                from backtest.research import run_research
                result = run_research(frames, args.symbol, info, start, end, args.output, args.calendar, args.folds)
                print(json.dumps(result, indent=2, default=str))
            else:
                result = BacktestEngine(args.symbol, artifact_dir=args.output, capabilities=capabilities,
                    symbol_info=info, strategy=StrategyEngine(MLPredictor(enabled=False)) if args.train_ml else None).run(train_ml=args.train_ml, data=frames,
                    start=start, end=end, strict_data=True, calendar=args.calendar)
                print(json.dumps(result["stats"], indent=2, default=str))
            return 0
        except (ValueError, RuntimeError, OSError) as exc:
            Path(args.output).mkdir(parents=True, exist_ok=True)
            quality = inspect_dataset(frames, start, end, args.calendar, True) if "frames" in locals() else {}
            (Path(args.output) / "blocked.json").write_text(json.dumps({"error": str(exc), "data_quality": quality}, indent=2), encoding="utf-8")
            logger.error(str(exc))
            return 2
        finally:
            if broker is not None:
                broker.disconnect()
    if args.command in {"paper", "live"}:
        return run_loop(args, args.command == "live")
    if args.command == "demo-report":
        print(json.dumps(export_demo_report(TradingStore(config.DATABASE_PATH), args.output), indent=2))
        return 0
    if args.command == "dashboard":
        import web_demo
        web_demo.PORT = args.port
        return web_demo.main()
    database = Path(config.DATABASE_PATH)
    store = TradingStore(database.with_stem(database.stem + "-live") if args.mode == "live" else database)
    broker = make_broker(config.SETTINGS.broker) if args.mode == "live" and args.command == "kill-switch" and args.cancel_pending else PaperBrokerAdapter(store)
    try:
        if args.command == "kill-switch":
            if args.cancel_pending:
                broker.connect()
            SafeExecutionService(broker, store).kill_switch(args.cancel_pending)
        else:
            SafeExecutionService(broker, store).reset_kill_switch()
    finally:
        broker.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
