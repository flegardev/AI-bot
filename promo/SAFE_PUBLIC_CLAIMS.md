# SAFE_PUBLIC_CLAIMS.md — ai-bot

---

## SAFE TO CLAIM

These statements are accurate, verifiable from the codebase, and do not make financial performance promises.

1. **"Python 3.10+ algorithmic trading strategy research tool"** — accurate stack description, educational framing.
2. **"Implements a multi-timeframe technical analysis pipeline: 1H EMA 200 bias, 15m liquidity sweep detection, 5m CHoCH/BOS entry signals"** — directly verifiable from the strategy code.
3. **"Includes walk-forward backtesting against historical OHLCV data via ccxt"** — verifiable from backtest module and ccxt dependency.
4. **"scikit-learn confidence filter trained from backtest results gates entry signals"** — verifiable from ML module.
5. **"Risk management enforced in code: 1% per trade, 2:1 minimum R:R, 3% daily loss circuit breaker, 10% drawdown circuit breaker"** — verifiable from risk management logic.
6. **"Three modes: backtest, paper trading (no real orders), and live (opt-in, Binance API required)"** — verifiable from mode handling code.
7. **"All trades logged to CSV; position state persisted to JSON"** — verifiable from logging and state management code.
8. **"Open-source educational project — not financial advice"** — consistent with README disclaimer and project intent.

---

## DO NOT CLAIM

These statements are either unverifiable, misleading, or would misrepresent the project's nature. Never use these in any public-facing content.

1. **Any specific win rate, return percentage, or profit figure** — backtest results vary by time period, symbol, and market conditions; historical performance does not predict future results. Never state or imply a number.
2. **"This strategy is profitable" or "this bot makes money"** — this frames a research tool as a financial product. The tool has no live track record and makes no guarantees.
3. **"Use this to trade live markets" or "deploy this to earn passive income"** — encourages real financial risk on an educational codebase; contradicts the project's documented purpose and could expose you to legal liability.
4. **"The ML model improves performance" (without qualification)** — the sklearn filter is trained on historical backtest data; its out-of-sample effectiveness is unproven. Safe version: "ML confidence filter trained on backtest results."
5. **Any comparison to commercial trading bots or professional trading systems** — ai-bot is a student/developer research project, not a production-grade trading system.
