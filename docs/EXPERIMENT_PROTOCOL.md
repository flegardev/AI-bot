# Experiment protocol v1

All commands in this protocol use historical simulation or `PaperBrokerAdapter`. No research component calls
the market adapter's order API. Keep `TRADING_MODE=paper`, `ENABLE_LIVE_TRADING=false`, and
`I_UNDERSTAND_REAL_MONEY_RISK=false`. No software result establishes profitability.

## Data and identity

Read [DATA_CONTRACT.md](DATA_CONTRACT.md) before importing data. Save UTC midpoint candles, `symbol.json`,
`contract.json`, quality reports and dataset hashes. Cache failures as quarantined observations; never replace
missing MT5 history with a different exchange or synthetic prices. A fresh artifact directory is required.

Each competition saves its predeclared candidate list, sample gate, UTC split times, seed (42), code and Git
identities, instrument economics, configuration and data hashes before evaluation. Every attempted run,
including failures and zero-trade runs, is appended to `experiments.jsonl`. Artifacts contain per-decision
snapshot hashes and their complete rejection funnel, execution/risk records, equity/drawdown/monthly curves,
regime tables and a SHA256 manifest. Successful artifacts cannot be overwritten by reusing their directory.

## Strategy selection

`compete` starts with flat, `smc_baseline_v1`, trend periods 20/30, and ATR breakout periods 20/30.
The bounded proposal space is trend/ATR periods {10,20,30,40,60}; maximum 10 specifications, budget at most
100 trials, default 48. Budget validation occurs before running a search. It is a trial budget, not a compute SLA.

`smc_baseline_v1` preserves original main's EMA-200 bias, equal-EMA SHORT tie, swing/equal-level thresholds,
latest-closed 15m sweep and 5m confirmation. The newer shared bias helper rejects an equal-EMA tie; it remains
available outside this original comparator. Inspection confirms that a detected 15m sweep stays visible during
the three 5m evaluations until the next 15m candle closes, then is replaced. This may miss later confirmations,
but no evidence here establishes that a longer persistence is better. No sweep-persistence variant or threshold
relaxation has been silently introduced.

Two rolling development folds select on 0..30% and 0..45%, then evaluate on 30..45% and 45..60%.
All candidates are also run on training 0..60% and validation 60..80%. A winner needs at least 30 validation
trades and positive net return; rank eligible candidates by validation net return. Save `selection.json`
before evaluating the selected candidate on 80..100%. If none qualifies, report `rejected_no_validation_edge`.
There is no fallback that chooses the least bad strategy.

The selected candidate receives final-period 0.75x/1.5x/2x cost stresses and a validation-only neighboring
parameter run. Flat is the final independent reference. Long and short exposure-matched references use the
same entry/exit midpoint observations, durations and volumes, with market liquidation costs. These are
conditional attribution calculations: strategy-chosen exit times are not a separately tradable benchmark.

Report UTC session, fixed descriptive volatility/trend/spread buckets, instrument and entry month. Do not
choose thresholds from favorable subgroups. Moving-block bootstrap requires 30 trades and estimates
conditional historical uncertainty, without adjusting away selection bias or guaranteeing a future return.
Recovery and turnover are descriptive metrics; neither is a qualification gate.

The local holdout registry flags repeated final-dataset access. It cannot prevent a human from moving the
registry or selecting ideas after reading final results. Any seen final period is development data for later
hypotheses; reserve a new period for a new confirmatory test. Never select from stress/final/forward outcomes.

## ML collection and chronological models

Use unfiltered rule trades with decision IDs, versioned numeric entry features, availability timestamps,
entry/exit times, net P&L, costs and label intervals. Exclude forced end-of-data closures, unresolved labels
and outcomes not yet observed. Missing schema/features fail; an insufficient sample creates a status report,
never a junk model. The default minimum is 200 total labeled examples; this is a software minimum, not proof.

Portfolio collection uses declared UTC 60/20/20 boundaries. Purge every label whose exit plus a five-minute
embargo overlaps the next partition. Fit preprocessing and estimators on the early 80% of training; calibrate
on the remaining training tail, with an additional purge. Sigmoid calibration uses a frozen fitted estimator.
No random cross-validation or default gradient-boosting early stopping can access future data.

Logistic regression, RandomForest and HistGradientBoosting use fixed, recorded hyperparameters. Two
additional purged/calibrated expanding folds lie entirely inside training and are reported as robustness
diagnostics. Validation chooses a filter only if its net P&L beats both zero and rule-only P&L, its drawdown
does not exceed rule-only, and at least 10 validation trades pass the unchanged 0.70 probability threshold.
The model is frozen before final metrics are computed. Appending or changing final labels cannot change
selection when UTC boundaries are held fixed (regression tested).

Standalone `MLPredictor.train(records)` retains a predeclared chronological row split for compatibility;
research commands pass fixed UTC boundaries so appended records cannot move selection cutoffs. Save model
cards, versions, dataset/schema hashes, fit/calibration/selection cutoffs and every candidate's success/failure.
`ml-compare` replays selected ML and SMC/trend/ATR/flat portfolios on the final window, including separate ML
cost stresses and regime artifacts. Trade-table filtering alone is not a portfolio performance result.

## Automated hypotheses

`ai-research` defaults to a deterministic hypothesis catalog, explicitly marked `llm_used=false`.
An optional local Ollama model can supply at most four schema-constrained hypotheses with falsification
conditions. Its text can select only allowed strategy names and periods; it cannot supply code, prices,
returns, indicator values or acceptance decisions. No final or forward observations enter the proposal
context. Record proposals, malformed inputs, all trials, errors and negative results in immutable-terminal
SQLite research rows and JSONL exports. Critic conclusions remain `unproven` or `rejected`.

The Ollama HTTP transport has synthetic schema tests. A real LLM invocation is a separate optional integration
test; do not imply it ran merely because a catalog workflow succeeded. RL/deep learning was not added.

## Frozen prospective paper evaluation

`freeze` accepts a selected specification and normalized source contract, instrument metadata and optional
approved SMC filter. It locks code, configuration, runtime versions, model bytes, selection hash/cutoff and
data contract. Market evaluation cannot be backdated. An unselected competition cannot be frozen as a winner.
Synthetic/unknown-trained models cannot qualify a market freeze. Starting a different freeze requires a new
directory; do not overwrite an evaluation. Only load trusted, locally produced joblib models.

`forward` reads the frozen source's market data only. Execution always uses a separate paper ledger.
Before each observation it checks the freeze, timestamps, completed candles, warm-up, gaps and bid/ask.
Existing positions advance on complete candles exactly once, including after restart, then on current quotes;
one OS writer lock prevents concurrent ledger loops. Entry-candle extrema before a mid-bar entry are excluded.
Quote spread is observed; historical fallback spread, percentage slippage and per-side fees remain explicit
simulation assumptions. Broker fee schedules, dynamic tick conversion and directional/triple swap are unverified.

Log decisions, risk rejects, simulated fills, quote-to-fill moves, source-to-receipt clock age, fee assumptions,
account snapshots, failures and available feature drift. Clock age is not a measured network round-trip or
actual broker execution latency. Drift uses fit-only feature means/std and reports accepted rule features;
it never retrains or changes thresholds. An unresolved in-flight entry blocks new orders for reconciliation.

Reports always say `unproven`: sufficient untouched prospective observations and independent execution checks
must be reviewed before any stronger research status. Synthetic forward replay tests software behavior only;
it cannot manufacture elapsed prospective evidence. This project does not authorize live promotion.
