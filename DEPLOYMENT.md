# Local paper and historical operation

Use Python 3.12 or 3.13. The container uses 3.13; the pinned coincurve dependency does not currently install reliably on 3.14.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe main.py diagnostic --output artifacts/diagnostic
.venv\Scripts\python.exe main.py check --broker mt5 --symbol EURUSD
.venv\Scripts\python.exe main.py paper --broker mt5 --symbol EURUSD --once
```

Keep TRADING_MODE=paper and both real-money opt-ins false. MT5 data requires a local Windows terminal. Linux Docker cannot supply that terminal; use explicitly cached UTC data for historical research. Never relabel Binance data as MT5.

```powershell
python main.py cache-data --broker mt5 --symbol EURUSD --start 2025-01-01 --end 2025-02-01 --calendar fx_ny --output datasets/eurusd
python main.py backtest --data-dir datasets/eurusd --symbol EURUSD --start 2025-01-01 --end 2025-02-01 --calendar fx_ny --output artifacts/eurusd
python main.py backtest --data-dir datasets/eurusd --symbol EURUSD --start 2025-01-01 --end 2025-02-01 --calendar fx_ny --train-ml --output artifacts/training
```

A failing quality manifest is a blocked experiment, not a zero-return result. Cached datasets and trained models are local artifacts. See docs/DATA_CONTRACT.md and RESEARCH_GUIDE.md for data and model assumptions.

Docker's default is the valid `paper --broker binance --symbol BTC/USDT` command. It uses a paper execution adapter; network/source availability is still required. Alternatively run a reproducible diagnostic without a market connection:

```sh
docker build -t ai-bot .
docker run --rm -v ./artifacts:/app/artifacts ai-bot diagnostic --output artifacts/diagnostic
```

The existing live broker interfaces are preserved for compatibility, but live execution is outside this research workflow. No implementation or validation command in this milestone invokes broker order APIs. ML is disabled by default and inadequate training samples produce an explicit status rather than a fabricated model.
