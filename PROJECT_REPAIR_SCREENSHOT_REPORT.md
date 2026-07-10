# Project Repair Screenshot Report

## Project

- Name: AI Trading Strategy Bot
- Local path: `C:\Users\tinif\Documents\ai-bot-main\ai-bot-main`
- Runtime URL: `http://127.0.0.1:8765`

## What was broken or incomplete

- The repository is a Python app, not a Node app. There is no `package.json` or package script layer.
- The actual app code was nested one folder below the provided working directory.
- No local `.env` existed, even though the README setup flow expects one.
- The browser demo existed but looked like a rough prototype: sparse layout, weak visual hierarchy, repeated card grids, inline spacing, and a typo in demo trade data.
- The local JSON endpoint did not send CORS or no-cache headers.
- The README did not document the safe browser demo command.

## What I fixed

- Added a safe local `.env` with blank Binance keys, paper mode, testnet enabled, and live trading disabled.
- Installed/verified Python dependencies inside the existing project virtual environment.
- Improved `web_demo.py` UI for the dashboard, strategy breakdown, and backtest report.
- Reworked demo cards, tables, metric states, sidebar, safe-mode copy, and dashboard rhythm.
- Fixed the bad demo trade label `Phonebook liquidity sweep`.
- Added CORS, cache, and content-type safety headers for local API use.
- Added `OPTIONS` support for the local API server.
- Documented the local browser demo in `README.md`.
- Captured three real desktop screenshots from the running app.

## Files changed

- `.env`
- `README.md`
- `web_demo.py`
- `_final_desktop_screenshots/01-main-dashboard-or-home.png`
- `_final_desktop_screenshots/02-key-feature.png`
- `_final_desktop_screenshots/03-detail-or-secondary-screen.png`
- `PROJECT_REPAIR_SCREENSHOT_REPORT.md`
- `run_logs/web_demo.stdout.log`
- `run_logs/web_demo.stderr.log`

## Commands used

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m py_compile web_demo.py main.py config.py logger.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
Test-NetConnection -ComputerName 127.0.0.1 -Port 8765 -InformationLevel Quiet
Start-Process -FilePath '.\.venv\Scripts\python.exe' -ArgumentList 'web_demo.py' -WorkingDirectory (Get-Location) -RedirectStandardOutput run_logs\web_demo.stdout.log -RedirectStandardError run_logs\web_demo.stderr.log -PassThru -WindowStyle Hidden
Invoke-WebRequest -Uri http://127.0.0.1:8765/api/evaluate -UseBasicParsing
```

Browser verification and screenshots were captured with Playwright at a 1440px desktop viewport.

## Env and demo setup

Created `.env` with safe local defaults:

```env
BINANCE_API_KEY=
BINANCE_API_SECRET=
USE_TESTNET=true
LIVE_TRADING_ENABLED=false
REQUIRE_TESTNET_FOR_LIVE=true
MODE=paper
SYMBOL=BTC/USDT
ACCOUNT_BALANCE=10000
```

The browser demo uses deterministic local OHLCV data and does not need API keys, network market data, or exchange order permissions.

## Verification results

- `pip install -r requirements.txt`: dependencies already satisfied in `.venv`
- `py_compile`: passed
- `python -m unittest discover -s tests -v`: 12 tests passed
- Browser pages checked:
  - `/`: `Trading Strategy Control Room`
  - `/strategy`: `Strategy Signal Breakdown`
  - `/backtest`: `Backtest Demo Report`
  - `/api/evaluate`: HTTP 200 JSON
- Browser console/page errors during screenshot capture: none

## Screenshots saved

- `_final_desktop_screenshots/01-main-dashboard-or-home.png`
- `_final_desktop_screenshots/02-key-feature.png`
- `_final_desktop_screenshots/03-detail-or-secondary-screen.png`

## Remaining issues

- PowerShell emitted `fatal: bad revision 'HEAD'` after test runs. This appears to come from Git/prompt tooling inspecting the parent repository, which has no first commit. I did not commit or alter Git history.
- Live trading remains intentionally disabled by default. Enabling it requires real credentials plus explicit operator confirmation.

## Exact commands to run again

```powershell
cd C:\Users\tinif\Documents\ai-bot-main\ai-bot-main
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe web_demo.py
```

Then open:

```text
http://127.0.0.1:8765
```
