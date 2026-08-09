"""
config.py — Central configuration for the trading bot.
All sensitive values (API keys, etc.) are loaded from .env.
Everything else is set here and imported across modules.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))

# ─── Exchange ─────────────────────────────────────────────────────────────────
EXCHANGE_ID = "binance"
API_KEY = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_API_SECRET", "")
USE_TESTNET = _env_bool("USE_TESTNET", True)
LIVE_TRADING_ENABLED = _env_bool("LIVE_TRADING_ENABLED", False)
REQUIRE_TESTNET_FOR_LIVE = _env_bool("REQUIRE_TESTNET_FOR_LIVE", True)

# ─── Symbol & Timeframes ──────────────────────────────────────────────────────
SYMBOL = os.getenv("SYMBOL", "BTC/USDT")

TF_BIAS = "1h"        # Trend bias via EMA 200
TF_LIQUIDITY = "15m"  # Liquidity sweep detection
TF_ENTRY = "5m"       # CHoCH / BOS confirmation

# Candles to fetch per timeframe (1H needs 200+ for EMA 200)
CANDLE_LIMIT = {
    "1h":  250,
    "15m": 100,
    "5m":  60,
}

# ─── Bot Mode ─────────────────────────────────────────────────────────────────
# backtest | paper | live
MODE = os.getenv("MODE", "paper")

# ─── Risk Management ─────────────────────────────────────────────────────────
RISK_PER_TRADE = 0.01         # Risk 1% of account per trade
MIN_RISK_REWARD = 2.0         # Minimum R:R ratio (1:2)
MAX_TRADES_PER_SESSION = 2    # Max trades per session/day
MAX_OPEN_POSITIONS = _env_int("MAX_OPEN_POSITIONS", 1)
MAX_DAILY_LOSS_PCT = _env_float("MAX_DAILY_LOSS_PCT", 0.03)
MAX_DRAWDOWN_PCT = _env_float("MAX_DRAWDOWN_PCT", 0.10)

# ─── Strategy Parameters ─────────────────────────────────────────────────────
EMA_PERIOD = 200              # EMA period for 1H bias
SWING_WINDOW = 5              # Bars on each side to confirm a swing point
EQUAL_LEVEL_THRESHOLD = 0.0015  # 0.15% tolerance for equal highs/lows
LIQUIDITY_LOOKBACK = 50       # Candles to scan for liquidity levels on 15m
STRUCTURE_LOOKBACK = 30       # Candles to scan for CHoCH/BOS on 5m

# ─── ML Filter ────────────────────────────────────────────────────────────────
USE_ML_FILTER = True
ML_CONFIDENCE_THRESHOLD = 0.70   # Minimum confidence to take a trade
ML_MODEL_PATH = "ml/model.pkl"
ML_MIN_SAMPLES = 30              # Minimum trades needed before ML is used
TRAIN_ML_AFTER_BACKTEST = _env_bool("TRAIN_ML_AFTER_BACKTEST", False)

# ─── Account ─────────────────────────────────────────────────────────────────
ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "10000"))

# ─── Backtest Settings ────────────────────────────────────────────────────────
BACKTEST_START = os.getenv("BACKTEST_START", "2024-01-01")
BACKTEST_END = os.getenv("BACKTEST_END", "2024-12-31")
INITIAL_BALANCE = float(os.getenv("INITIAL_BALANCE", "10000"))
BACKTEST_FEE_RATE = _env_float("BACKTEST_FEE_RATE", 0.001)
BACKTEST_SLIPPAGE_PCT = _env_float("BACKTEST_SLIPPAGE_PCT", 0.0005)

# ─── Logging ─────────────────────────────────────────────────────────────────
TRADE_LOG_FILE = "trades.csv"
STATE_DIR = os.getenv("STATE_DIR", "state")
PAPER_STATE_FILE = os.path.join(STATE_DIR, "paper_state.json")
LIVE_STATE_FILE = os.path.join(STATE_DIR, "live_reconciliation.json")
