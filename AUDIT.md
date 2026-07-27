# Production hardening audit

## Entry points and preserved behavior

- `main.py` is the production CLI.
- `web_demo.py` serves the read-only operational dashboard.
- `StrategyEngine` preserves the existing EMA-200 bias, liquidity-sweep, and CHoCH/BOS rules without parameter optimization.
- Binance spot support remains isolated in `BinanceBrokerAdapter`; it is long-only.

## Problems found and fixes

| Area | Problem found | Fix |
|---|---|---|
| Time | Frames were sliced by candle open time, exposing unfinished 1H/15m/5m candles | Central close-time helpers, normalization, and close-aware HTF alignment |
| Parity | Backtest and live orchestration duplicated strategy decisions | One broker-independent `StrategyEngine` |
| Models | Trade/account/order state used loosely shaped dictionaries | Typed shared dataclasses and enums |
| Sizing | `risk / price distance` ignored ticks, contracts, lot steps, margin, minimum stops | MT5 metadata/profit/margin-aware sizing that only rounds down |
| Account risk | Legacy paths used balance/free cash and in-memory peaks | Equity, free margin, floating P&L, durable daily equity/high-water state |
| Execution | Entry protection was not verified; retries could duplicate | Initial SL/TP, post-fill verification, attach retry, emergency close, idempotent client IDs, critical cooldown |
| Reconciliation | Local in-memory orders were treated as truth | Broker position/order snapshot reconciliation persisted in SQLite |
| Paper | Exit checks used only one price | OHLC intrabar touches, gaps, spread, fees, slippage, margin, long/short, conservative ambiguous bars |
| Backtest | Future exit scan skipped the normal event loop and metrics/artifacts were incomplete | Sequential event loop, next-bar latency, broker capabilities, paper broker fills, complete artifacts and assumptions |
| ML | Random train/test split leaked temporal structure; only accuracy was reported | Chronological train/validation/test, expanding-window walk-forward, schema/version metadata, baseline trading comparison |
| Dashboard | Production dashboard displayed deterministic fake profits | Reads JSON/CSV/SQLite only and displays `No data available.` otherwise |
| Configuration | Scattered unchecked constants and weak live gate | Validated settings and three independent live gates; paper default |
| Logging | Console/CSV only | Rotating structured application, trade, risk, broker, and critical incident logs |
| Testing | No candle-close, MT5 sizing, ambiguous bars, chronology, or emergency-close coverage | Mocked MT5 and comprehensive deterministic safety tests |

## Files affected

Created:

- `core/models.py`, `core/timeframes.py`, `core/strategy_engine.py`
- `brokers/base.py`, `brokers/mt5.py`, `brokers/binance.py`, `brokers/paper.py`
- `risk/sizing.py`, `risk/engine.py`
- `execution/service.py`, `execution/reconciliation.py`
- `persistence/store.py`
- `requirements-dev.txt`, `pyproject.toml`
- new test modules under `tests/`

Replaced or updated:

- `main.py`, `config.py`, `logger.py`, `backtest/engine.py`, `ml/predictor.py`
- `data/fetcher.py`, `web_demo.py`, `.env.example`, `requirements.txt`, `README.md`

Legacy `execution/executor.py` and `risk/manager.py` remain for backward-compatible imports and existing callers. Production CLI paths use the new adapters and services.

## Behavior-changing corrections

- A candle becomes visible at open timestamp plus timeframe, never at its open timestamp.
- Signals execute no earlier than the configured next-bar latency.
- Binance spot does not simulate or place shorts.
- Minimum lots are rejected when they would exceed risk.
- Both-exit bars default to stop first; gaps fill at the adverse open.
- ML is disabled by default and does not block baseline trades when disabled.
- No hard-coded demo profits remain in the dashboard.

## Remaining limitations

- MT5 dry-run and live integration require a locally running, broker-connected Windows terminal.
- OHLC cannot reconstruct within-bar tick order.
- MT5 fill modes and symbol suffixes are broker-specific.
- Deal-history reconciliation is less detailed than full broker statement ingestion.
- A technically correct engine can still implement an unprofitable strategy.

## Exact commands

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
python -m mypy core brokers risk execution persistence ml backtest main.py web_demo.py

python main.py check --broker mt5 --symbol EURUSD
python main.py backtest --broker mt5 --symbol EURUSD --start 2024-01-01 --end 2024-12-31
python main.py paper --broker mt5 --symbol EURUSD
python main.py live --broker mt5 --symbol EURUSD
python main.py dashboard --port 8000
```

Before the live command, explicitly set `TRADING_MODE=live`, `ENABLE_LIVE_TRADING=true`, and `I_UNDERSTAND_REAL_MONEY_RISK=true`. Run `check` first.
