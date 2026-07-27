# MT5-first trading bot

This repository contains a conservative, testable trading engine for backtest, paper, MT5, and isolated Binance spot execution. The same `StrategyEngine` creates broker-independent `TradeSignal` objects in every mode. Broker adapters own data access, symbol constraints, sizing, and execution translation.

Real-money trading is disabled by default. Technical correctness and a clean backtest do not establish profitability.

## Architecture

- `core/`: typed candles, signals, orders, fills, positions, account state, closed-candle helpers, and the shared strategy engine.
- `brokers/`: protocol plus MT5, Binance spot, and realistic paper adapters.
- `risk/`: MT5 tick-value/lot sizing and shared equity-based risk controls.
- `execution/`: protected-order execution, kill switch, and broker reconciliation.
- `persistence/`: SQLite events, durable risk state, and idempotency records.
- `backtest/`: event-driven simulation and artifact generation.
- `ml/`: chronological splits and expanding-window evaluation; disabled by default.
- `web_demo.py`: read-only dashboard backed by artifacts and SQLite only.

## Windows installation

1. Install Python 3.13 (64-bit).
2. Install and open the 64-bit MetaTrader 5 terminal supplied by your broker.
3. Log in manually in MT5 and enable algorithmic trading only when you are ready to run live.
4. In PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

The official `MetaTrader5` Python package communicates with a local Windows terminal. It does not work as a headless Linux container integration.

## Configuration

Fill these in `.env` without committing the file:

```dotenv
TRADING_MODE=paper
BROKER=mt5
MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=
MT5_TERMINAL_PATH=
MT5_SYMBOL=EURUSD
MT5_MAGIC_NUMBER=260727
ENABLE_LIVE_TRADING=false
I_UNDERSTAND_REAL_MONEY_RISK=false
```

Risk, spread, slippage, latency, fee, database, and artifact settings are documented in `.env.example`. Invalid fractions, balances, modes, and broker names are rejected on import.

## Commands

Read-only MT5 validation (never places an order):

```powershell
python main.py check --broker mt5 --symbol EURUSD
```

Backtest with actual broker history:

```powershell
python main.py backtest --broker mt5 --symbol EURUSD --start 2024-01-01 --end 2024-12-31
```

Continuous MT5-data/paper-execution mode (Ctrl+C to stop), or one cycle for validation:

```powershell
$env:TRADING_MODE = "paper"
python main.py paper --broker mt5 --symbol EURUSD
python main.py paper --broker mt5 --symbol EURUSD --once
```

Live MT5 requires every gate:

```powershell
$env:TRADING_MODE = "live"
$env:ENABLE_LIVE_TRADING = "true"
$env:I_UNDERSTAND_REAL_MONEY_RISK = "true"
python main.py check --broker mt5 --symbol EURUSD
python main.py live --broker mt5 --symbol EURUSD
python main.py live --broker mt5 --symbol EURUSD --once
```

Run the actual-data dashboard:

```powershell
python main.py dashboard --port 8000
```

The dashboard displays `No data available.` until artifacts or persisted paper/live state exist.

Kill new entries without closing existing positions:

```powershell
python main.py kill-switch --cancel-pending
python main.py reset-kill-switch
```

## Verification

```powershell
python -m pytest -q
python -m ruff check .
python -m mypy core brokers risk execution persistence ml backtest main.py web_demo.py
```

Tests mock MT5 and never require a broker login or submit real orders.

## Backtest artifacts

Each run writes:

- `artifacts/backtest_summary.json`
- `artifacts/trades.csv`
- `artifacts/equity_curve.csv`
- `artifacts/drawdown_curve.csv`
- `artifacts/monthly_returns.csv`
- `artifacts/backtest_config_snapshot.json`

The snapshot includes spread, commissions, slippage, latency, gap handling, and the conservative stop-first assumption for bars touching both exits.

## Common errors

- `MT5 initialize failed`: open the correct 64-bit terminal or set `MT5_TERMINAL_PATH`.
- `MT5 login failed`: verify login, password, server spelling, and terminal connectivity. Credentials are never logged.
- `symbol not found`: use the broker-specific Market Watch name (for example `EURUSD.a`) in `MT5_SYMBOL`.
- `minimum volume would exceed allowed risk`: the trade is intentionally rejected; do not raise the risk merely to meet the minimum lot.
- `stop violates broker minimum distance`: widen the strategy stop or use a compatible symbol; the adapter will not silently alter it.
- live gate errors: all three settings (`TRADING_MODE`, `ENABLE_LIVE_TRADING`, and the risk acknowledgement) must be explicit.

## Limitations

- OHLC backtests cannot know tick order; simultaneous SL/TP touches use the conservative stop-first rule.
- MT5 symbol/session behavior varies by broker and must be checked against that broker's demo account.
- Binance support is isolated spot/long-only. Native OCO availability varies; failed protection triggers an immediate market-close attempt.
- Reconciliation detects broker/local divergence, but broker deal-history enrichment is limited to the state returned by the adapter.
- No strategy parameters were optimized during hardening. ML stays disabled unless chronological out-of-sample results improve return and do not worsen drawdown after costs.
