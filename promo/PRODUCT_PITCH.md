# PRODUCT_PITCH.md — ai-bot

## One-Sentence Pitch

A Python-based algorithmic trading strategy research tool that automates multi-timeframe technical analysis and backtesting — built for learning, not for financial advice.

---

## Product Description

**ai-bot** is an open-source educational project that implements a structured, multi-timeframe technical analysis pipeline in Python. It codifies a real trading strategy — using EMA 200 bias on the 1H chart, liquidity sweep detection on 15m, and CHoCH/BOS entry signals on 5m — into backtestable, inspectable code. The goal is to help developers and traders understand how strategy logic works under the hood, not to generate profit.

Beyond simple backtesting, ai-bot includes a machine learning confidence filter (scikit-learn) trained from historical backtest results, walk-forward validation to reduce overfitting, paper trading mode for real-time simulation without real orders, and strict risk management rules enforced in code. Every mode logs trades to CSV and persists position state to JSON, giving researchers a clear audit trail of strategy behavior.

---

## Best 5 Features

- **Multi-timeframe analysis pipeline** — 1H EMA 200 trend bias feeds into 15m liquidity sweep detection and 5m CHoCH/BOS entry signals, all automated
- **Walk-forward backtesting** — validates strategy logic against historical data using rolling windows to surface overfitting risks
- **ML confidence filter** — sklearn model trained on backtest results gates live/paper signals, adding a data-driven layer to entry decisions
- **Risk management enforced in code** — 1% per trade, 2:1 minimum R:R, 3% daily loss circuit breaker, 10% drawdown circuit breaker
- **Three operating modes** — backtest (historical), paper (real-time simulation), live (opt-in, requires Binance API keys) — clearly separated for safe experimentation

---

## 30-Second Demo Flow

1. Clone the repo and install dependencies with `pip install -r requirements.txt`
2. Run `python main.py --mode backtest --symbol BTCUSDT --start 2023-01-01 --end 2023-12-31`
3. Watch the terminal output as candles are processed: bias detection, sweep signals, and CHoCH/BOS entries print in real time
4. When the backtest completes, open `trades_log.csv` to inspect every simulated trade with entry, exit, R:R, and outcome
5. Run `python main.py --mode paper --symbol BTCUSDT` to switch to live market data with no real orders placed

---

## Target Audience

- Python developers curious about algorithmic trading concepts
- Traders who want to study technical analysis strategy logic through code
- Students and self-learners exploring quantitative finance and ML integration
- Engineers who want to see how backtesting, risk management, and live data pipelines fit together

> **Disclaimer:** ai-bot is a research and educational tool. It is not financial advice, does not guarantee any financial outcome, and should not be used as the basis for real investment decisions.
