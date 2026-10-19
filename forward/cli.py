"""Read-only price acquisition around the paper-only frozen runner."""
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import config
from core.quotes import Quote
from data.contracts import MarketDataContract
from data.fixtures import diagnostic_fixture
from forward.frozen import freeze_candidate
from forward.runner import ForwardPaperRunner


def diagnostic(output):
    root = Path(output)
    frames, info, start, _ = diagnostic_fixture(root / "fixture")
    contract = MarketDataContract(**frames["5m"].attrs["data_contract"])
    selection = {"selected": {"name": "no_trade", "period": 20},
        "selection_end": str(start - pd.Timedelta(minutes=5)), "protocol_id": "synthetic-predeclared-flat-check"}
    freeze_candidate(selection, contract, root / "frozen", symbol_info=info, start=start)
    runner = ForwardPaperRunner(root / "frozen" / "freeze.json")
    for when in pd.date_range(start, periods=12, freq="5min"):
        quote = Quote(99.99995, 100.00005, when.to_pydatetime())
        runner.observe(frames, quote, received_at=when.to_pydatetime())
    return runner.export()


def run(args, market):
    runner = ForwardPaperRunner(args.freeze)
    frozen = runner.manifest
    contract = MarketDataContract(**frozen["data_contract"])
    if contract.dataset_kind != "market" or contract.source != f"{args.broker}:{frozen['symbol']}":
        raise ValueError("real-time feed must match the frozen market contract; synthetic replay uses forward-diagnostic")
    if args.poll_seconds < 1:
        raise ValueError("poll interval must be positive")
    try:
        market.connect()
        # Brokerage methods are never passed to the execution service.
        if asdict(market.get_symbol_info(frozen["symbol"])) != frozen["symbol_info"]:
            raise ValueError("broker symbol contract changed; create a separately reviewed freeze")
        while True:
            frames = {tf: market.get_candles(frozen["symbol"], tf) for tf in ("1h", "15m", "5m")}
            cursor = runner.store.get_state(f"paper_last_bar_{frozen['symbol']}")
            if cursor and (frames["5m"].empty or pd.Timestamp(cursor) < frames["5m"].index[0]):
                frames["5m"] = market.get_candles(frozen["symbol"], "5m", pd.Timestamp(cursor).to_pydatetime(), datetime.now(timezone.utc))
            for frame in frames.values():
                if args.broker == "mt5":
                    frame.loc[:, ["open", "high", "low", "close"]] += config.BACKTEST_SPREAD / 2
                frame.attrs["data_contract"] = asdict(contract)
            result = runner.observe(frames, market.get_quote(frozen["symbol"]))
            print(json.dumps(result, default=str))
            if args.once:
                return 0
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        runner.store.record_event("forward_failure", {"error": str(exc), "phase": "read_only_market_acquisition"}, mode="paper")
        print(json.dumps({"blocked": str(exc)}))
        return 2
    finally:
        runner.export()
        market.disconnect()
