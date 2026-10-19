# Trading research and demo workflow

This project does not have a demonstrated profitable edge. Software verification, historical strategy selection,
paper execution, and actual broker demo execution are separate evidence. None automatically authorizes real-money trading.

## Start safely

Create a virtual environment, install `requirements-dev.txt`, and copy `.env.example` to `.env`.
Keep `ENABLE_LIVE_TRADING=false`, `I_UNDERSTAND_REAL_MONEY_RISK=false`, and `TRADING_MODE=paper`.
Use your broker's demo terminal and set `MT5_TERMINAL_PATH`. Do not commit credentials, account files or generated models.

The complete [experiment protocol](docs/EXPERIMENT_PROTOCOL.md) specifies selection, leakage controls,
cost assumptions, bounded hypotheses and frozen forward rules. Start with the offline reproduction:

```powershell
python scripts/reproduce.py --output artifacts/offline-evidence-001
```

It runs diagnostic, strategy competition, ML portfolio comparison, real three-model training on artificial
labels, catalog research automation, and a frozen forward replay. Every dataset is labeled SYNTHETIC.
It prints actual subprocess results and writes logs, `reproduction.json` and a SHA256 manifest. It requires
no MT5, exchange connection, API credentials, Ollama or network request. Use a fresh output path each time.

```powershell
python main.py check --broker mt5 --symbol EURUSD
python main.py paper --broker mt5 --symbol EURUSD --once
python main.py demo-report --output artifacts/demo
```

Quotes must have valid positive bid/ask, an explicit timestamp, acceptable spread, and age within
`STALE_PRICE_SECONDS`. Future timestamps beyond five seconds are rejected too. Correct the source clock/time
convention before proceeding; the bot never silently subtracts a guessed broker offset or fabricates freshness.
MT5 documents UTC for Python history: https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesrange_py

Paper mode replays completed bars once across restarts, processes SL/TP, marks positions, and observes the current
quote. Bars that started before a mid-bar entry cannot reconstruct post-entry extrema; sampled quotes cover
those positions until a complete post-entry bar becomes available. A missing replay interval blocks resumption.
Paper fills remain simulated; they are not actual demo-broker fills.

## Historical data and reproducible backtests

```powershell
python main.py cache-data --broker mt5 --symbol EURUSD --start 2025-01-01 --end 2026-01-01 --calendar fx_ny --output artifacts/eurusd-cache
python main.py backtest --data-dir artifacts/eurusd-cache --symbol EURUSD --start 2025-01-01 --end 2026-01-01 --calendar fx_ny --output artifacts/baseline
```

The window is `[start, end)` in UTC. Download includes a separate warm-up buffer; trading and statistics only
use the evaluation window. Every required timeframe must have sufficient warm-up and complete expected bars
in both warm-up and evaluation. Validation rejects duplicate/unordered timestamps, NaN/infinite values, invalid
OHLC, nonpositive prices, negative volume, and missing coverage. No price forward-filling is used.

The `fx_ny` calendar handles Sunday/Friday 17:00 New York and DST. The legacy `fx` calendar is an explicit
approximation: Friday/Sunday 22:00 UTC. Neither knows broker holidays or maintenance breaks.
Review `data_quality.json`; a calendar mismatch must be resolved with documented
source/session information. Do not change the calendar just to hide missing data. `24/7` is for continuous data.

Strict runs fail before writing performance statistics if data fails. Use a fresh output directory for each run.
Direct Python `BacktestEngine.run(..., strict_data=False)` is available for diagnostic fixtures; its quality
status remains in the report and must not be treated as validated trading evidence.

Offline data is also supported:

```powershell
python main.py backtest --data-dir datasets/eurusd --symbol EURUSD --start 2025-01-01 --end 2026-01-01 --calendar fx_ny --output artifacts/offline
```

Supply `1h.csv`, `15m.csv`, `5m.csv` with `timestamp,open,high,low,close,volume`, explicit UTC timestamps,
and sufficient warm-up. Supply `contract.json` as specified in [DATA_CONTRACT.md](docs/DATA_CONTRACT.md).
Normalized cache prices are midpoint OHLC. Supply `symbol.json` with the `SymbolInfo` fields:

```json
{"symbol":"EURUSD","tick_size":0.00001,"tick_value":1.0,"contract_size":100000,"volume_min":0.01,"volume_max":100,"volume_step":0.01,"leverage":100}
```

These are illustrative instrument values, not verified broker/account economics. A research run saves its data
and symbol metadata in the same format so it can be replayed offline.

## Execution and cost assumptions

- Decisions use closed candles. Orders execute at a later bar's open (`BACKTEST_LATENCY_BARS >= 1`), before
  that bar's exits are processed. Actual simulation timestamps are persisted.
- Midpoint bars become bid/ask using `BACKTEST_SPREAD` in absolute price units. MT5 Forex bid history is
  converted to midpoint using this configured constant spread; this is not historical variable-spread evidence.
- `BACKTEST_SLIPPAGE_PCT` is a fraction of execution price, applied adversely to market entries and stop exits.
  TP limits receive no favorable gap improvement. Stops win when both exits are touched in one OHLC bar.
- Legacy `BACKTEST_FEE_RATE * contract_size` is the commission in account currency per lot per side.
  Confirm that implied amount with the broker. Entry commission reduces equity immediately; records include both sides.
- `BACKTEST_SWAP_PER_LOT_DAY` is a constant daily debit in account currency per lot. Directional rates,
  positive credits and triple rollover are not modeled.
- P&L uses symbol tick economics consistently with sizing. Tick value is held constant; historical currency
  conversion and dynamic margin are not modeled. Gap risk can exceed the planned stop loss.

The simulator remains an OHLC approximation. Lower timeframe/tick replay, historical spreads, broker-specific
commission/rollover and currency conversion are needed before making strong execution claims.

## Understand zero trades before tuning

`strategy_diagnostics.json` counts missing history, no bias, no sweep, bias/sweep mismatch, missing entry,
invalid stops, unavailable directions and ML rejection/overlap. `backtest_summary.json` separates those
strategy gates from risk/order rejection reasons. Trades retain signal time, execution time, numerical entry
features, costs, exit reason, net P&L and net-profit labels. Do not lower thresholds simply to generate activity.

`signal_decisions.csv` and JSON contain decision IDs, closed-frame snapshot hashes and passed/rejected/not-reached
stages. Execution and portfolio-active records explain other blocked cycles. `summary.md` is readable even
with zero trades. `run_status.json`, quality reports and the SHA256 artifact manifest distinguish a completed
run from a blocked or interrupted attempt. `regime_report.csv` and conditional exposure references add context.

## ML training and evaluation

```powershell
python main.py ml-compare --data-dir datasets/eurusd --symbol EURUSD --start 2023-01-01 --end 2025-01-01 --calendar fx_ny --output artifacts/ml-comparison
```

`backtest --train-ml` also works. Training collects unfiltered rule trades, requires `ML_MIN_SAMPLES`, and
uses declared UTC 60/20/20 boundaries in portfolio research. Forced/unresolved/future outcomes are excluded.
Exits plus a five-minute embargo cannot overlap the next partition. Fit-only scaling, a purged chronological
calibration tail and training-only rolling folds evaluate logistic regression, RF and HistGradientBoosting.
Validation alone selects an eligible filter at the unchanged probability threshold. Final outcomes never
change selection. Model cards contain all candidates, versions, cutoffs, hyperparameters and hashes.

Keep `USE_ML_FILTER=false` until you review evidence. When explicitly enabled, the shared strategy invokes
the saved filter. Missing/unapproved models and signals inside the model's training/selection window block
entries. Old models are rejected. `ml_training.json` states when samples or label classes are insufficient.
`ml-compare` performs independent final portfolio replays against SMC/trend/ATR/flat and selected ML cost
stresses. If training is insufficient, it reports ML ineligible and still runs the simple references. The
standalone row-split training API is retained for compatibility; use fixed UTC cutoffs for research.

## Baselines, walk-forward research and uncertainty

```powershell
python main.py compete --data-dir datasets/eurusd --symbol EURUSD --start 2023-01-01 --end 2026-01-01 --calendar fx_ny --budget 48 --output artifacts/competition
```

The default set contains flat, unchanged-original SMC, EMA trend periods 20/30, and ATR-adjusted breakout
periods 20/30. Two rolling development folds precede fixed 60/20/20 training/validation/final partitions.
A winner needs positive validation net return and 30 validation trades. No qualifier means rejection.
Risk limits remain active. `research --folds` preserves the earlier exploratory research interface; use
`compete` for the current fixed-budget protocol and original SMC comparator.

Reports include all candidate attempts, a source/config/data manifest, data CSV snapshots, development
parameter comparisons, a final selected holdout, 0.75x/1.5x/2x cost stresses, and a seeded moving-block
bootstrap interval for expectancy when there are at least 30 holdout trades. A local registry flags repeated
evaluation of the same dataset. This is not protection against every form of research leakage: do not use
holdout/stress results to retune and then call them untouched. Bootstrap intervals are conditional estimates,
not guarantees. A one-day smoke test or 30 trades is not sufficient proof of a durable edge.

Repeat on independently sourced, verified data across different market conditions. Compare net expectancy,
drawdown/recovery, regime stability, nearby parameters, cost sensitivity and uncertainty. Keep an experiment
ledger, including failed trials. Overfitting reference: https://escholarship.org/uc/item/4w1110bb

## Frozen demo observations and execution safeguards

Optional hypothesis automation uses the same deterministic competition:

```powershell
python main.py ai-research --data-dir datasets/eurusd --symbol EURUSD --start 2023-01-01 --end 2026-01-01 --calendar fx_ny --budget 48 --output artifacts/agent-research
```

Default proposals come from a labeled catalog, without an LLM. Add `--ollama-model YOUR_INSTALLED_MODEL`
to use an already running local Ollama server, or `--proposals hypotheses.json` for a JSON list following
`ai_research.agents.Hypothesis`. No model installation is automatic. Invalid proposals and failed runs are
preserved in the SQLite/JSONL registry. LLM text never computes trading results or changes acceptance gates.

Freeze a genuinely selected strategy, using that run's `selection.json` and validated market cache:

```powershell
python main.py freeze --selection artifacts/competition/RUN_ID/selection.json --data-dir datasets/eurusd --output artifacts/frozen-001
python main.py forward --freeze artifacts/frozen-001/freeze.json --broker mt5 --once
```

For an approved SMC filter, add `--model PATH_TO_SELECTED_MODEL.pkl` to `freeze`. Market forward starts now
and cannot be backdated. `forward` verifies source/config/model/runtime/contract locks on every observation,
never retrains, owns a separate paper ledger, rejects old/duplicate/out-of-order events, and records failures,
quote age/spread, simulated cost/fill observations and available fit-reference feature drift. The data source
must match the frozen contract. Broker order APIs are never used. Remove `--once` for ongoing fresh-data
logging; Ctrl+C closes the read-only connection. A failing quote/history gate must be resolved first.

`python main.py forward-diagnostic --output artifacts/forward-fixture-001` runs a separately labeled
synthetic replay. It tests the machinery; it does not represent elapsed prospective trading.

Demo exports include configuration/model/source fingerprints, quote validity, blocked cycles, paper account
snapshots and closed trades. Changes create a new configuration fingerprint. Freeze the chosen configuration,
collect independent fresh-data observations, and compare actual broker demo fills separately before considering
capital. Long-term forward evidence cannot be generated instantly by a code upgrade.

Live and paper idempotency/state databases are separate. MT5 revalidates freshness/spread/price movement
immediately before submission. Slippage is in price units and converted to MT5 deviation points. Uncertain
submissions leave a durable in-flight reservation and disable entries; a restart or kill-switch reset cannot
silently retry them. Protection checks use the exact position ID and verify SL/TP values. Failed protection
disables entries before attempting an emergency close.

```powershell
python main.py kill-switch --mode live
```

Reconcile uncertain submissions against actual broker orders/deals/positions before manually resolving any
`pending_entry` reservation. Kill-switch reset alone intentionally does not resolve ambiguous broker state.
Live Binance execution is disabled until it has equivalent verified safeguards.

## Verification

```powershell
python -m pytest
ruff check .
mypy ai_research backtest brokers core data execution forward ml persistence risk strategy config.py logger.py main.py web_demo.py
```

CI runs pytest on supported Python 3.12 and 3.13. This includes pytest function tests that the old unittest-only CI skipped.
Python 3.14 currently hits an upstream coincurve source-build packaging failure; the container uses Python 3.13
with an available binary wheel. Upstream issue: https://github.com/ofek/coincurve/issues/208
