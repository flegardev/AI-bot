"""
strategy/bias.py — 1H trend bias using EMA 200.

Rules:
  - Price ABOVE EMA 200  →  bias = 'LONG'
  - Price BELOW EMA 200  →  bias = 'SHORT'
  - Not enough data      →  bias = None
"""
import pandas as pd

import config


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average (custom, no TA-Lib needed)."""
    return series.ewm(span=period, adjust=False).mean()


def get_bias(df_1h: pd.DataFrame) -> str | None:
    """
    Determine the current trend bias from the 1H chart.

    Args:
        df_1h: 1H OHLCV DataFrame (must have at least EMA_PERIOD rows)

    Returns:
        'LONG'  — price is above EMA 200
        'SHORT' — price is below EMA 200
        None    — insufficient data
    """
    if len(df_1h) < config.EMA_PERIOD:
        return None

    ema = calculate_ema(df_1h["close"], config.EMA_PERIOD)
    current_price = df_1h["close"].iloc[-1]
    current_ema   = ema.iloc[-1]

    if current_price > current_ema:
        return "LONG"
    elif current_price < current_ema:
        return "SHORT"
    return None   # Exactly on the EMA — no clear bias


def get_bias_details(df_1h: pd.DataFrame) -> dict:
    """
    Return bias and supporting details for logging / ML features.

    Returns dict with keys:
        bias, price, ema_200, ema_distance, ema_distance_pct
    """
    if len(df_1h) < config.EMA_PERIOD:
        return {"bias": None}

    ema = calculate_ema(df_1h["close"], config.EMA_PERIOD)
    price     = df_1h["close"].iloc[-1]
    ema_value = ema.iloc[-1]
    distance  = price - ema_value
    dist_pct  = distance / ema_value

    return {
        "bias":             "LONG" if price > ema_value else "SHORT" if price < ema_value else None,
        "price":            price,
        "ema_200":          ema_value,
        "ema_distance":     distance,
        "ema_distance_pct": dist_pct,
    }
