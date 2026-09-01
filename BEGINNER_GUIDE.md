# 🎓 AI Quantitative Trading Engine — Beginner's Learning Guide

## What This Project Does
**ai-bot** is a multi-timeframe algorithmic trading bot implementing Smart Money Concepts (SMC) price action trading, Machine Learning signal filters, fixed fractional risk management, and automated order execution for Forex (MetaTrader 5) and Crypto (Binance).

---

## 🗺️ Architecture Map

```
ENTRY POINT
  │  (main.py - Main Quantitative Loop)
  ▼
DATA INGESTION & MARKET FEED
  │  (data/fetcher.py - MT5 / CCXT Multi-timeframe OHLCV Candlestick Feed)
  ▼
MARKET STRUCTURE & SMC STRATEGY
  │  ├── 4H Higher-Timeframe Trend Bias (strategy/mtf.py)
  │  ├── 15M Liquidity Sweep Detection (strategy/liquidity.py)
  │  └── 5M CHoCH & BOS Entry Triggers (strategy/entry.py)
  ▼
MACHINE LEARNING CONFIDENCE FILTER
  │  (ml/predictor.py - Random Forest / LightGBM Probability Gate)
  ▼
RISK MANAGEMENT & POSITION SIZING
  │  (risk/manager.py - Fixed Fractional Risk Math, ATR Buffering, R:R >= 1:2)
  ▼
TRADE EXECUTION & TELEGRAM NOTIFICATIONS
     (execution/executor.py - Limit/Market Order Routing, notification/telegram.py)
```

---

## 🎯 Core Files to Study First (Recommended Learning Order)

1. [`main.py`](file:///C:/Users/tinif/repos/ai-bot/main.py)
   - **Why study first**: Contains the master trading loop coordinating data feeds, analysis strategies, risk gates, and execution.
2. [`strategy/entry.py`](file:///C:/Users/tinif/repos/ai-bot/strategy/entry.py)
   - **Why study next**: Teaches Smart Money Concepts (SMC) price action formulas: Market Structure, Change of Character (CHoCH), and Break of Structure (BOS).
3. [`risk/manager.py`](file:///C:/Users/tinif/repos/ai-bot/risk/manager.py)
   - **Why study third**: Explains the mathematical formula for fixed fractional position sizing and risk-to-reward ratio enforcement.
4. [`data/fetcher.py`](file:///C:/Users/tinif/repos/ai-bot/data/fetcher.py)
   - **Why study fourth**: Demonstrates how multi-timeframe OHLCV candlestick data is fetched and formatted with Pandas.
5. [`execution/executor.py`](file:///C:/Users/tinif/repos/ai-bot/execution/executor.py)
   - **Why study fifth**: Shows real-world API order routing and slippage protection.

---

## 🛠️ How to Run & Verify

1. **Verify Python Code**:
   ```bash
   python -m py_compile main.py strategy/entry.py risk/manager.py
   ```
2. **Run Backtesting Engine**:
   ```bash
   python backtest/engine.py
   ```
