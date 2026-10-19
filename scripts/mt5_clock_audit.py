"""Record actual MT5 clock evidence. Never calls order APIs or adjusts timestamps."""
import argparse
import json
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--terminal")
    parser.add_argument("--output", required=True, help="fresh directory for read-only audit observations")
    args = parser.parse_args()
    root = Path(args.output)
    if root.exists() and any(root.iterdir()):
        raise ValueError("use a fresh audit directory; prior observations cannot be overwritten")
    root.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.update(TRADING_MODE="paper", ENABLE_LIVE_TRADING="false", I_UNDERSTAND_REAL_MONEY_RISK="false")
    if args.terminal:
        os.environ["MT5_TERMINAL_PATH"] = args.terminal
    from brokers.mt5 import MT5BrokerAdapter
    from data.clock_audit import collect_clock_audit
    adapter = MT5BrokerAdapter()
    try:
        report = collect_clock_audit(adapter, args.symbol)
    except Exception as exc:
        report = {"status": "blocked_acquisition", "error": str(exc), "order_apis_invoked": False,
                  "source_contract_verified": False}
    finally:
        adapter.disconnect()
    (root / "clock_audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 2 if str(report["status"]).startswith("blocked") else 0


if __name__ == "__main__":
    sys.exit(main())
