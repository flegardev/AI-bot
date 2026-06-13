# SHORT_VIDEO_SCRIPT.md — ai-bot

**Format:** 30-second screen recording with voiceover
**Tool shown:** Terminal (dark theme recommended)

---

## Hook — 0 to 5 seconds

**Screen:** Terminal window opens. Cursor blinks on a blank line.

**Voiceover:**
"I built a Python tool that automates a multi-timeframe trading strategy — and backtests it against a full year of data."

---

## Show — 5 to 20 seconds

**Screen (5–9s):** Command typed and executed:
```
python main.py --mode backtest --symbol BTCUSDT --start 2023-01-01 --end 2023-12-31
```

**Screen (9–14s):** Terminal scrolls through live output — lines like:
```
[1H] EMA200 bias: BULLISH
[15m] Liquidity sweep detected at 42,310
[5m] CHoCH confirmed — entry signal generated
[TRADE] LONG | Entry: 42,315 | SL: 41,890 | TP: 43,165 | R:R 2.01
```

**Screen (14–20s):** Backtest summary prints:
```
Backtest complete: 312 signals | 89 trades taken | ML filter applied
trades_log.csv written.
```
Then a quick scroll through `trades_log.csv` open in the terminal — columns visible: date, symbol, direction, entry, exit, R:R, outcome.

**Voiceover (5–20s):**
"It runs a 1H trend bias check, spots 15-minute liquidity sweeps, and fires entries on 5-minute CHoCH or BOS confirmation. An sklearn model filters low-confidence signals. Every trade logs to CSV."

---

## Close — 20 to 30 seconds

**Screen:** README open in a browser tab — headline visible. Then GitHub repo page with star count.

**Voiceover:**
"It's a research tool — not financial advice. Open source, Python 3.10+, free to clone and study. Link in bio."

**On-screen text overlay:**
```
Educational research tool — not financial advice.
github.com/[your-username]/ai-bot
```
