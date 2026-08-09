# Deployment — AI Trading Bot

A Python **worker/CLI process** (not a web server). Runs a continuous loop in
`paper` mode by default; `backtest` is one-shot; `live` trades real money and
requires explicit opt-in. There is no HTTP port or health endpoint — deploy it
as a **background worker**, not a web service.

## Runtime
- Python **3.13 or 3.14** (targeted by CI; the image uses `python:3.14-slim`).
- Direct dependencies are pinned in `requirements.txt`; the full transitive environment is pinned in `requirements.lock`.

| Command | Purpose |
|---------|---------|
| `python -m venv .venv && . .venv/bin/activate` | Create env |
| `pip install -r requirements.lock` | Reproducible install |
| `python -m unittest discover -s tests -v` | Run the unit and live-safety mock tests |
| `python main.py --mode backtest` | One-shot backtest |
| `python main.py --mode paper` | Real-time paper trading (no real orders) — **default** |
| `python main.py --mode live` | LIVE trading (real money; gated, see below) |

## Docker
```bash
docker build -t ai-bot .
# Paper mode (safe default), persist state to a volume:
docker run -d --name ai-bot \
  --env-file .env \
  -v ai-bot-state:/app/state \
  ai-bot                       # == --mode paper
# Backtest (one-shot):
docker run --rm --env-file .env ai-bot --mode backtest
```
The image is configured to run as non-root (`appuser`) on Python 3.14. Build and
test the exact revision before promoting it.

## Recommended hosting (background worker)
| Priority | Platform | Notes |
|----------|----------|-------|
| **Primary** | **Railway** or **Render** *background/worker* service | No public port. Set env vars in the dashboard; attach a disk mounted at `/app/state`. Restart-on-failure. |
| Fallback | **Fly.io** (process group, no `[http_service]`) or a **VPS** | On a VPS run the Docker image with `--restart unless-stopped`, or a `systemd` unit. Mount a volume for `/app/state`. |

Do **not** deploy to Vercel/Netlify (no long-running process / no serverless fit).

## Environment variables (server-only) — see `.env.example`
| Variable | Default | Notes |
|----------|---------|-------|
| `MODE` | `paper` | `backtest` \| `paper` \| `live` |
| `BINANCE_API_KEY` / `BINANCE_API_SECRET` | empty | Only needed for live/testnet order placement. |
| `USE_TESTNET` | `true` | Keep true until validated. |
| `LIVE_TRADING_ENABLED` | `false` | Must be `true` **and** console-confirmed to trade live. |
| `REQUIRE_TESTNET_FOR_LIVE` | `true` | Safety gate before real-money trading. |
| `MAX_DAILY_LOSS_PCT` / `MAX_DRAWDOWN_PCT` / `MAX_OPEN_POSITIONS` | 0.03 / 0.10 / 1 | Kill switches (enforced; test-covered). |
| `ACCOUNT_BALANCE` | `10000` | Simulation/sizing input for backtest and paper flows only; never a live fallback. |
| `STATE_DIR` | `state` (`/app/state` in Docker) | Persist paper state and `live_reconciliation.json` here. |

## Safety (verified by tests)
- Live trading is **disabled by default** and requires `LIVE_TRADING_ENABLED=true`.
- Live trading refuses to run unless testnet validation is satisfied when `REQUIRE_TESTNET_FOR_LIVE=true`.
- Live balance and open-order reconciliation failures stop execution; the configured simulation balance is never substituted.
- Binance spot entries use atomic OCO exit protection when the pinned adapter exposes it. The fallback path cancels partial exits and closes the new fill; incomplete compensation is durably marked for manual reconciliation and blocks restart.
- Daily-loss, drawdown, position-count, and session guards block invalid/over-limit trades.
- Never commit real API keys. Use platform secret stores / `--env-file`.

## ML model — not shipped, train before relying on it
No `ml/model.pkl` is committed (the file is git-ignored). The ML confidence
filter is **ON by default**, and an untrained model returns a neutral `0.5`
confidence, which is below `ML_CONFIDENCE_THRESHOLD` (0.70) — so **no trades are
taken until you either train a model or disable the filter**. The bot prints a
clear warning at startup when this is the case.

Two supported options:
```bash
# 1. Train a model from a backtest (saves ml/model.pkl on the pinned sklearn):
python main.py --mode backtest --train-ml

# 2. Or run without the ML filter (rule-based signals only):
python main.py --mode paper --no-ml
```
`joblib` model files use Python pickle semantics and can execute code while
loading. Only use a locally trained or otherwise trusted, integrity-controlled
`ml/model.pkl`; never download and load an untrusted model. A version-incompatible
model may fail to load and disable the filter, but that error handling is not a
security sandbox.
