# SCREENSHOT_LIST.md — ai-bot

Capture all screenshots from a terminal with a dark theme (e.g., VS Code integrated terminal or iTerm2 with a dark background). Use a large font size (14–16pt) for readability.

---

## Screenshot 1

**Filename:** `screenshot_backtest_running.png`

**What to show:** The terminal mid-backtest — live output scrolling with lines showing EMA bias detection, liquidity sweep detection, and CHoCH/BOS entry signals firing for BTCUSDT.

**What to highlight:** The three-timeframe logic chain visible in sequence: `[1H]` → `[15m]` → `[5m]` → `[TRADE]` lines appearing in order.

**Why it matters:** Immediately shows the core multi-timeframe pipeline working in real time — the most technically impressive part of the project at a glance.

---

## Screenshot 2

**Filename:** `screenshot_backtest_summary.png`

**What to show:** The end-of-backtest summary block in the terminal — total signals scanned, trades taken after ML filter, and the confirmation that `trades_log.csv` was written.

**What to highlight:** The ML filter line (signals in vs. trades taken) — shows the confidence filter actually reducing noise.

**Why it matters:** Gives viewers a clean "result" moment — the backtest finished, the data is saved, the tool works end to end.

---

## Screenshot 3

**Filename:** `screenshot_trades_csv.png`

**What to show:** `trades_log.csv` open in the terminal (e.g., `column -t -s, trades_log.csv | head -20`) showing columns: date, symbol, direction, entry price, exit price, R:R ratio, outcome.

**What to highlight:** The R:R column — showing enforced 2:1 minimum — and the outcome column showing a mix of results (not all wins).

**Why it matters:** Demonstrates transparency: the tool logs everything, including losses. Supports the honest "research tool" framing.

---

## Screenshot 4

**Filename:** `screenshot_paper_mode.png`

**What to show:** Terminal running `python main.py --mode paper --symbol BTCUSDT` — output showing real-time candle processing, bias detection, and a "No signal this candle" or "Signal filtered by ML" line.

**What to highlight:** The mode header at startup: `[MODE: PAPER TRADING — no real orders will be placed]`

**Why it matters:** Shows the safe experimentation mode clearly — reinforces the educational/research positioning and shows the tool is live-data capable without financial risk.

---

## Screenshot 5

**Filename:** `screenshot_risk_management.png`

**What to show:** A terminal output snippet where the daily loss circuit breaker or drawdown circuit breaker fires — e.g., `[CIRCUIT BREAKER] Daily loss limit reached (3%). Trading halted for today.`

**What to highlight:** The circuit breaker message itself and the clean halt — no further trades attempted after the trigger.

**Why it matters:** Proves the risk management is real and enforced in code, not just documented — one of the strongest technical differentiators of this project.
