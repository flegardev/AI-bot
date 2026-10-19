# Synthetic fixture registry

`data.fixtures.diagnostic_fixture(path)` writes constant midpoint OHLCV, full 220-hour warm-up,
instrument metadata, a 24/7 synthetic contract and a NOTICE file. Evaluation spans six hours from
2024-01-15 UTC. It is deliberately a zero-edge fixture, useful for coverage, no-trade diagnostics,
artifact generation, competition rejection and frozen forward replay.

`data.fixtures.labeled_model_fixture()` creates 300 artificial alternating labels and numeric features
to exercise real model fitting, chronological calibration, selection and serialization. Labels are
intentionally separable; its classifier scores and P&L numbers are not trading performance.

Other tests explicitly construct adversarial OHLC events: next-open gaps, both barriers touched,
long/short costs, missing bars, invalid values, future mutations and transaction crashes. No fixture
is substituted for a live MT5 error. Generated datasets/models stay outside source control.
