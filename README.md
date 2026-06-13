# AI Trading Strategy Bot

An open-source algorithmic trading research tool built in Python. Implements a multi-timeframe technical analysis strategy with backtesting, paper trading simulation, and an optional ML confidence filter — all configurable, all transparent.

> **Important:** This is a research and educational tool. Backtest results do not guarantee future performance. Paper mode is the safe default. Live trading requires explicit opt-in and your own Binance API credentials. Never risk money you cannot afford to lose.

---

## What it does

- **Backtest** a rule-based strategy against historical OHLCV data
- **Paper trade** in real-time using live market data — no real orders placed
- **Simulate** entry signals using multi-timeframe logic (1H bias, 15m sweep, 5m entry)
- **Score signals** with an optional sklearn ML confidence filter
- **Log everything** — signals, trades, P&L, drawdown — to CSV and console

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

- Python 3.10+
- Internet access for live market data via `ccxt`
- Binance API keys only required for live mode

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Linux/Mac
# .\.venv\Scripts\Activate.ps1    # Windows PowerShell

pip install -r requirements.txt
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

---

## Limitations and honest notes

- The ML filter is experimental. It is trained on backtest data and may not generalise.
- Paper mode simulates fills at close price — real fills may differ due to slippage.
- Live mode requires Binance spot. Short trades are not supported on spot.
- Before using live capital: validate on the Binance testnet first, add order reconciliation, set up operator alerts, and run extended out-of-sample validation.
- Past backtest performance does not predict future results.

---

## License

MIT

---

Made by FlegarTech. If this project helped you, you can [support development](https://paypal.me/TiniFlegar).
