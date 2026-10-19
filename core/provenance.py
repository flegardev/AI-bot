"""Stable source/config identities, excluding credentials and generated datasets."""
import hashlib
import importlib.metadata
import json
import platform
import subprocess
from pathlib import Path

import config


def source_hash():
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for folder in ("backtest", "brokers", "core", "data", "execution", "ml", "persistence", "risk", "strategy", "ai_research", "forward"):
        for path in sorted((root / folder).glob("*.py")):
            digest.update(str(path.relative_to(root)).replace("\\", "/").encode())
            digest.update(path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").encode())
    for name in ("main.py", "config.py", "logger.py", "requirements.lock", "pyproject.toml"):
        digest.update((root / name).read_text(encoding="utf-8-sig").replace("\r\n", "\n").encode())
    return digest.hexdigest()


def runtime_identity():
    packages = {}
    for name in ("pandas", "numpy", "scikit-learn", "joblib", "threadpoolctl", "ccxt", "MetaTrader5"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "not_installed"
    return {"python": platform.python_version(), "packages": packages}


def configuration():
    names = ("INITIAL_BALANCE", "RISK_PER_TRADE", "MIN_RISK_REWARD", "MAX_OPEN_POSITIONS", "MAX_DAILY_LOSS_PCT", "MAX_DRAWDOWN_PCT",
             "EMA_PERIOD", "SWING_WINDOW", "EQUAL_LEVEL_THRESHOLD", "LIQUIDITY_LOOKBACK", "STRUCTURE_LOOKBACK",
             "BACKTEST_FEE_RATE", "BACKTEST_SLIPPAGE_PCT", "BACKTEST_SPREAD", "BACKTEST_SWAP_PER_LOT_DAY", "BACKTEST_LATENCY_BARS",
             "ML_CONFIDENCE_THRESHOLD", "TF_BIAS", "TF_LIQUIDITY", "TF_ENTRY")
    result = {name: getattr(config, name) for name in names}
    result.update(max_spread=config.SETTINGS.max_spread, max_slippage=config.SETTINGS.max_slippage,
                  quote_max_age=config.SETTINGS.stale_price_seconds,
                  max_symbol_exposure=config.SETTINGS.max_symbol_exposure_pct,
                  max_portfolio_risk=config.SETTINGS.max_portfolio_risk_pct)
    return result


def digest_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def git_revision():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1],
                              capture_output=True, text=True, timeout=5, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
