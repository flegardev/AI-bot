# Deployment — AI Trading Bot

A Python **worker/CLI process** (not a web server). Runs a continuous loop in
`paper` mode by default; `backtest` is one-shot; `live` trades real money and
requires explicit opt-in. There is no HTTP port or health endpoint — deploy it
as a **background worker**, not a web service.

## Runtime
- Python **3.13 or 3.14** (verified on both; image uses `python:3.14-slim`).
- Dependencies pinned in `requirements.txt`; full transitive lock in `requirements.lock`.

| Command | Purpose |
|---------|---------|
| `python -m venv .venv && . .venv/bin/activate` | Create env |
| `pip install -r requirements.lock` | Reproducible install |
| `python -m unittest discover -s tests` | Run tests (12, all pass) |
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
Image runs as non-root (`appuser`), Python 3.14.6. Verified: builds, imports, all
12 tests pass inside the container.

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
| `STATE_DIR` | `state` (`/app/state` in Docker) | Persist paper positions/logs here. |

## Safety (verified by tests)
- Live trading is **disabled by default** and requires `LIVE_TRADING_ENABLED=true`.
- Live trading refuses to run unless testnet validation is satisfied when `REQUIRE_TESTNET_FOR_LIVE=true`.
- Daily-loss and stop-loss-side guards block invalid/over-limit trades.
- Never commit real API keys. Use platform secret stores / `--env-file`.

## Known limitation — ML model version
`ml/model.pkl` was trained with scikit-learn 1.7.2; the pinned runtime is 1.9.0,
so loading emits an `InconsistentVersionWarning`. The predictor still loads and
**degrades gracefully** (returns neutral confidence on failure), and the ML
filter is optional (`--no-ml`). To remove the warning and guarantee fidelity,
retrain the model on the pinned stack:
```bash
python main.py --mode backtest --train-ml   # regenerates ml/model.pkl
```
