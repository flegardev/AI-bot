<p align="center"><strong>FLEGAR DEV</strong><br><sub>Software · IT · Full-stack</sub></p>

# AI Trading Strategy Bot

A Python algorithmic-trading research project built around a transparent, rule-based multi-timeframe strategy. It includes backtesting, persistent paper trading, a local browser demo, and an optional scikit-learn confidence filter.

> **Important:** This is a research and educational tool. Backtest results do not guarantee future performance. Paper mode is the safe default. Live trading requires explicit opt-in and your own Binance API credentials. Never risk money you cannot afford to lose.

---

## What it does

- **Backtest** a rule-based strategy against historical OHLCV data
- **Paper trade** in real-time using live market data — no real orders placed
- **Simulate** entry signals using multi-timeframe logic (1H bias, 15m sweep, 5m entry)
- **Score signals** with an optional sklearn ML confidence filter
- **Record decisions** — signals and completed trades go to CSV/console; live actions also use a durable reconciliation journal

---

## Strategy logic

1. Fetch 1H, 15m, and 5m candles from Binance (via `ccxt`)
2. Determine directional bias from the 1H EMA 200
3. Detect a 15m liquidity sweep aligned with the bias
4. Confirm entry on the 5m chart using CHoCH / BOS structure
5. Size the position using configurable risk rules (1% risk per trade default)
6. Optionally apply the ML confidence filter (trained from backtest results)
7. Execute via paper executor (simulated) or live executor (real orders, opt-in only)

---

## Project structure

```text
.
├── backtest/      walk-forward backtesting engine
├── data/          Binance/ccxt OHLCV data fetching
├── execution/     paper and live order execution
├── ml/            ML confidence filter and model persistence
├── risk/          position sizing and trade construction
├── strategy/      bias, liquidity sweep, and entry logic
├── tests/         smoke tests
├── config.py      all runtime configuration
├── logger.py      structured console and trade logging
└── main.py        CLI entry point
```

---

## Requirements

- Python 3.13 or 3.14 (the CI matrix targets both; the container uses Python 3.14)
- Internet access for live market data via `ccxt`
- Binance API keys only required for live mode

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Linux/Mac
# .\.venv\Scripts\Activate.ps1    # Windows PowerShell

pip install -r requirements.lock
cp .env.example .env
```

Edit `.env` — for backtest and paper trading, API keys can be left blank:

```env
BINANCE_API_KEY=
BINANCE_API_SECRET=
USE_TESTNET=true
MODE=paper
SYMBOL=BTC/USDT
ACCOUNT_BALANCE=10000
BACKTEST_START=2024-01-01
BACKTEST_END=2024-12-31
INITIAL_BALANCE=10000
```

---

## Usage

```bash
# Run a backtest (no API keys needed)
python main.py --mode backtest

# Backtest and retrain the ML filter
python main.py --mode backtest --train-ml

# Paper trading — live data, no real orders
python main.py --mode paper

# Paper trading without ML filter
python main.py --mode paper --no-ml

# Different symbol
python main.py --mode paper --symbol ETH/USDT

# Live trading — requires credentials + explicit env flag
python main.py --mode live
```

Paper mode uses real-time data but never places real orders. Position state is saved in `state/paper_state.json` and closed trades are written to `trades.csv`.

`ACCOUNT_BALANCE` is a simulation/sizing input for backtest, paper, and local demo flows. Live mode sizes from a freshly verified exchange balance and stops if that balance cannot be read; it never substitutes the configured $10,000 demo default.

### Live-mode safety boundary

Live mode is disabled by default and should be treated as experimental. When explicitly enabled, it:

- requires credentials, testnet while `REQUIRE_TESTNET_FOR_LIVE=true`, and an interactive `YES` confirmation;
- fails closed when balance or open-order reconciliation cannot be verified;
- journals entry/protection/compensation state in `state/live_reconciliation.json` before placing real orders;
- uses Binance's atomic OCO order-list endpoint for paired take-profit and stop-loss protection when exposed by the pinned `ccxt` adapter; and
- cancels any partial exit order and market-closes the newly acquired asset if an adapter without OCO rejects either protection leg. Incomplete compensation is persisted as `manual_reconciliation_required` and blocks restart.

This is defense in depth, not a claim of production readiness. Exchange outages, fills, fees, race conditions, API changes, and operator actions still require independent monitoring and manual reconciliation.

### Local browser demo

For a safe visual demo with deterministic local data, run:

```bash
python web_demo.py
```

Then open `http://127.0.0.1:8765`. The dashboard, strategy breakdown, backtest report, and `/api/evaluate` endpoint use the real strategy and risk modules without API keys or exchange orders.

---

## Risk configuration

Key defaults in `config.py`:

| Setting | Default | Description |
| ------- | ------- | ----------- |
| `RISK_PER_TRADE` | 1% | Max account risk per trade |
| `MIN_RISK_REWARD` | 2.0 | Minimum R:R before entry |
| `MAX_OPEN_POSITIONS` | 1 | Max concurrent positions |
| `MAX_DAILY_LOSS_PCT` | 3% | Daily loss circuit breaker |
| `MAX_DRAWDOWN_PCT` | 10% | Total drawdown circuit breaker |
| `ML_CONFIDENCE_THRESHOLD` | 0.70 | Minimum ML score to take trade |

---

## Tests

```bash
python -m unittest discover -s tests -v
```

The safety suite includes mocked balance, reconciliation, entry, take-profit, stop-loss, compensation, and atomic OCO paths. It does not submit exchange orders.

---

## ML model trust boundary

`ml/model.pkl` is a Python `joblib`/pickle artifact. Loading it can execute code. Only load a model you trained locally or received through a trusted, integrity-controlled channel; never load an untrusted or downloaded `.pkl` file. The model is ignored by Git and is not included in this repository.

---

## Limitations and honest notes

- The ML filter is experimental. It is trained on backtest data and may not generalise.
- Paper mode simulates fills at close price — real fills may differ due to slippage.
- Live mode targets Binance spot. Short trades are not supported.
- Existing open orders block new entries, but the project does not continuously reconcile every external account action or provide operator alerts.
- Before considering live capital: validate on Binance testnet, add independent monitoring, and run extended out-of-sample validation.
- Past backtest performance does not predict future results.

---

## License status

This repository currently has no `LICENSE` file. Do not assume permission to copy, modify, or redistribute it without authorization from the owner.

---

Built by [Tini Flegar](https://github.com/flegardev).
