# AI Trading Bot — production worker image
FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install pinned dependencies first for better layer caching.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
RUN python -m pip uninstall --yes pip setuptools \
    && python -c "import ccxt, pandas, sklearn"

# App source.
COPY . .

# Persisted runtime state (paper positions, logs) lives here; mount a volume.
ENV STATE_DIR=/app/state
RUN mkdir -p /app/state && \
    useradd --create-home --uid 10001 appuser && \
    chown -R appuser:appuser /app
USER appuser

# Safe default: paper trading (no real orders). Override the mode at runtime,
# e.g. docker run ... ai-bot backtest --broker binance --symbol BTC/USDT
ENTRYPOINT ["python", "main.py"]
CMD ["paper", "--broker", "binance", "--symbol", "BTC/USDT"]
