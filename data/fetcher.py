"""
data/fetcher.py — Fetch OHLCV candle data from Binance via ccxt.

Returns clean pandas DataFrames with columns:
  timestamp, open, high, low, close, volume
"""
import time

import ccxt
import pandas as pd

import config
import logger
from core.timeframes import filter_closed_candles


def create_exchange(include_credentials: bool = False, sandbox: bool = False) -> ccxt.binance:
    """
    Build an isolated ccxt exchange instance.

    Market-data fetches intentionally use a separate public instance so live
    trading sandbox/credential settings cannot leak into normal data reads.
    """
    params = {
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    }
    if include_credentials:
        params["apiKey"] = config.API_KEY
        params["secret"] = config.API_SECRET

    exchange = ccxt.binance(params)
    if sandbox:
        exchange.set_sandbox_mode(True)
    return exchange


def _build_exchange() -> ccxt.binance:
    """
    Instantiate the ccxt Binance exchange object.
    NOTE: sandbox mode is NOT set here — market data (OHLCV) is always fetched
    from the real Binance API (it is public and free).
    Sandbox mode is only used in LiveTrader when placing actual orders.
    """
    return create_exchange(include_credentials=False, sandbox=False)


# Module-level singleton — rebuilt once per process
_exchange: ccxt.binance | None = None


def get_exchange() -> ccxt.binance:
    global _exchange
    if _exchange is None:
        _exchange = _build_exchange()
    return _exchange


def reset_exchange():
    """Force a fresh exchange connection (useful after config changes)."""
    global _exchange
    _exchange = None


def fetch_ohlcv(
    symbol: str,
    timeframe: str,
    limit: int | None = None,
    since: int | None = None,   # Unix ms timestamp
    retries: int = 3,
) -> pd.DataFrame:
    """
    Fetch OHLCV candles and return a clean DataFrame.

    Args:
        symbol:    e.g. 'BTC/USDT'
        timeframe: e.g. '1h', '15m', '5m'
        limit:     number of candles (uses config defaults if None)
        since:     start timestamp in milliseconds (optional)
        retries:   number of retry attempts on failure

    Returns:
        DataFrame with columns [timestamp, open, high, low, close, volume]
        index is DatetimeIndex (UTC)
    """
    exchange = get_exchange()

    if limit is None:
        limit = config.CANDLE_LIMIT.get(timeframe, 100)

    for attempt in range(retries):
        try:
            raw = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
            break
        except ccxt.NetworkError as e:
            if attempt < retries - 1:
                logger.warning(f"Network error fetching {timeframe} data, retrying... ({e})")
                time.sleep(2)
            else:
                raise
        except ccxt.ExchangeError as e:
            logger.error(f"Exchange error: {e}")
            raise

    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)

    # Drop any duplicate timestamps (exchange quirk)
    df = df[~df.index.duplicated(keep="last")]
    df.sort_index(inplace=True)

    return filter_closed_candles(df, timeframe)


def fetch_all_timeframes(symbol: str) -> dict[str, pd.DataFrame]:
    """
    Convenience function — fetch all three timeframes at once.

    Returns:
        {'1h': df_1h, '15m': df_15m, '5m': df_5m}
    """
    logger.info(f"Fetching {symbol} candles for 1h / 15m / 5m ...")
    return {
        config.TF_BIAS:      fetch_ohlcv(symbol, config.TF_BIAS),
        config.TF_LIQUIDITY: fetch_ohlcv(symbol, config.TF_LIQUIDITY),
        config.TF_ENTRY:     fetch_ohlcv(symbol, config.TF_ENTRY),
    }


def fetch_historical_range(
    symbol: str,
    timeframe: str,
    start: str,   # 'YYYY-MM-DD'
    end: str,
) -> pd.DataFrame:
    """
    Fetch a large historical range by paginating ccxt calls.
    Used by the backtesting engine.

    Args:
        symbol:    e.g. 'BTC/USDT'
        timeframe: e.g. '1h'
        start:     start date string 'YYYY-MM-DD'
        end:       end date string 'YYYY-MM-DD'

    Returns:
        Combined DataFrame covering the full date range
    """
    exchange = get_exchange()

    since_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    end_ms   = int(pd.Timestamp(end,   tz="UTC").timestamp() * 1000)

    all_candles: list = []
    page_limit = 1000  # Binance allows up to 1000 candles per call

    logger.info(f"Downloading {symbol} {timeframe} history: {start} → {end}")

    while since_ms < end_ms:
        try:
            raw = exchange.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=page_limit)
        except Exception as e:
            logger.error(f"Error paginating {timeframe}: {e}")
            time.sleep(5)
            continue

        if not raw:
            break

        all_candles.extend(raw)
        since_ms = raw[-1][0] + 1  # Move forward past the last fetched candle
        time.sleep(exchange.rateLimit / 1000)  # Respect rate limits

    if not all_candles:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)
    df = df[~df.index.duplicated(keep="last")]
    df.sort_index(inplace=True)

    # Trim to exact requested range
    df = df[df.index < pd.Timestamp(end, tz="UTC")]

    logger.success(f"  Downloaded {len(df)} candles for {timeframe}")
    return filter_closed_candles(df, timeframe, pd.Timestamp(end, tz="UTC"))
