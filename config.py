"""Validated environment configuration. Defaults are paper-only and conservative."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    if value not in {"1", "0", "true", "false", "yes", "no", "on", "off"}:
        raise ValueError(f"{name} must be a boolean")
    return value in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


@dataclass(frozen=True)
class Settings:
    trading_mode: str = os.getenv("TRADING_MODE", os.getenv("MODE", "paper")).lower()
    broker: str = os.getenv("BROKER", "mt5").lower()
    symbol: str = os.getenv("MT5_SYMBOL", os.getenv("SYMBOL", "EURUSD"))
    enable_live_trading: bool = _bool("ENABLE_LIVE_TRADING", False)
    acknowledge_real_money_risk: bool = _bool("I_UNDERSTAND_REAL_MONEY_RISK", False)
    risk_per_trade: float = _float("RISK_PER_TRADE", 0.01)
    max_daily_loss_pct: float = _float("MAX_DAILY_LOSS_PCT", 0.03)
    max_drawdown_pct: float = _float("MAX_DRAWDOWN_PCT", 0.10)
    max_open_positions: int = _int("MAX_OPEN_POSITIONS", 1)
    max_portfolio_risk_pct: float = _float("MAX_PORTFOLIO_RISK_PCT", 0.03)
    max_symbol_exposure_pct: float = _float("MAX_SYMBOL_EXPOSURE_PCT", 1.0)
    max_spread: float = _float("MAX_SPREAD", 0.0005)
    max_slippage: float = _float("MAX_SLIPPAGE", 0.0005)
    stale_price_seconds: int = _int("STALE_PRICE_SECONDS", 120)
    initial_balance: float = _float("INITIAL_BALANCE", 10_000)
    state_dir: Path = Path(os.getenv("STATE_DIR", "state"))
    artifact_dir: Path = Path(os.getenv("ARTIFACT_DIR", "artifacts"))

    def validate(self) -> None:
        if self.trading_mode not in {"backtest", "paper", "live"}:
            raise ValueError("TRADING_MODE must be backtest, paper, or live")
        if self.broker not in {"mt5", "binance"}:
            raise ValueError("BROKER must be mt5 or binance")
        for name, value in (
            ("RISK_PER_TRADE", self.risk_per_trade),
            ("MAX_DAILY_LOSS_PCT", self.max_daily_loss_pct),
            ("MAX_DRAWDOWN_PCT", self.max_drawdown_pct),
            ("MAX_PORTFOLIO_RISK_PCT", self.max_portfolio_risk_pct),
        ):
            if not 0 < value <= 1:
                raise ValueError(f"{name} must be within (0, 1]")
        if self.max_open_positions < 1:
            raise ValueError("MAX_OPEN_POSITIONS must be positive")
        if self.initial_balance <= 0:
            raise ValueError("INITIAL_BALANCE must be positive")

    def assert_live_allowed(self) -> None:
        if self.trading_mode != "live":
            raise RuntimeError("TRADING_MODE must explicitly be live")
        if not self.enable_live_trading:
            raise RuntimeError("ENABLE_LIVE_TRADING must explicitly be true")
        if not self.acknowledge_real_money_risk:
            raise RuntimeError("I_UNDERSTAND_REAL_MONEY_RISK must explicitly be true")


SETTINGS = Settings()
SETTINGS.validate()

# Compatibility constants used by the preserved strategy modules.
EXCHANGE_ID = "binance"
API_KEY = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_API_SECRET", "")
USE_TESTNET = _bool("USE_TESTNET", True)
LIVE_TRADING_ENABLED = _bool("LIVE_TRADING_ENABLED", False)
REQUIRE_TESTNET_FOR_LIVE = _bool("REQUIRE_TESTNET_FOR_LIVE", True)
SYMBOL = SETTINGS.symbol
MODE = SETTINGS.trading_mode
TF_BIAS, TF_LIQUIDITY, TF_ENTRY = "1h", "15m", "5m"
CANDLE_LIMIT = {"1h": 250, "15m": 100, "5m": 100}
RISK_PER_TRADE = SETTINGS.risk_per_trade
MIN_RISK_REWARD = _float("MIN_RISK_REWARD", 2.0)
MAX_TRADES_PER_SESSION = _int("MAX_TRADES_PER_SESSION", 2)
MAX_OPEN_POSITIONS = SETTINGS.max_open_positions
MAX_DAILY_LOSS_PCT = SETTINGS.max_daily_loss_pct
MAX_DRAWDOWN_PCT = SETTINGS.max_drawdown_pct
EMA_PERIOD = _int("EMA_PERIOD", 200)
SWING_WINDOW = _int("SWING_WINDOW", 5)
EQUAL_LEVEL_THRESHOLD = _float("EQUAL_LEVEL_THRESHOLD", 0.0015)
LIQUIDITY_LOOKBACK = _int("LIQUIDITY_LOOKBACK", 50)
STRUCTURE_LOOKBACK = _int("STRUCTURE_LOOKBACK", 30)
USE_ML_FILTER = _bool("USE_ML_FILTER", False)
ML_CONFIDENCE_THRESHOLD = _float("ML_CONFIDENCE_THRESHOLD", 0.70)
ML_MODEL_PATH = os.getenv("ML_MODEL_PATH", "ml/model.pkl")
ML_MIN_SAMPLES = _int("ML_MIN_SAMPLES", 200)
TRAIN_ML_AFTER_BACKTEST = _bool("TRAIN_ML_AFTER_BACKTEST", False)
ACCOUNT_BALANCE = _float("ACCOUNT_BALANCE", SETTINGS.initial_balance)
BACKTEST_START = os.getenv("BACKTEST_START", "2024-01-01")
BACKTEST_END = os.getenv("BACKTEST_END", "2024-12-31")
INITIAL_BALANCE = SETTINGS.initial_balance
BACKTEST_FEE_RATE = _float("BACKTEST_FEE_RATE", 0.0005)
BACKTEST_SLIPPAGE_PCT = _float("BACKTEST_SLIPPAGE_PCT", 0.0001)
BACKTEST_SPREAD = _float("BACKTEST_SPREAD", 0.0001)
BACKTEST_LATENCY_BARS = _int("BACKTEST_LATENCY_BARS", 1)
BACKTEST_SWAP_PER_LOT_DAY = _float("BACKTEST_SWAP_PER_LOT_DAY", 0.0)
MARKET_CALENDAR = os.getenv("MARKET_CALENDAR", "fx")
TRADE_LOG_FILE = os.getenv("TRADE_LOG_FILE", "trades.csv")
STATE_DIR = str(SETTINGS.state_dir)
PAPER_STATE_FILE = str(SETTINGS.state_dir / "paper_state.json")
LIVE_STATE_FILE = str(SETTINGS.state_dir / "live_state.json")
DATABASE_PATH = str(SETTINGS.state_dir / "trading.sqlite3")
ARTIFACT_DIR = str(SETTINGS.artifact_dir)
