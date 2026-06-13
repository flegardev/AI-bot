"""
strategy/liquidity.py — 15m liquidity sweep detection.

Concepts implemented:
  - Swing highs / lows  (local extremes using a rolling window)
  - Equal highs / lows  (two swing points within EQUAL_LEVEL_THRESHOLD of each other)
  - Liquidity sweep     (price wicks THROUGH an equal level, then CLOSES back on the other side)

Return values:
  'bullish' — equal LOWS were swept  (price grabbed liquidity below, likely to move up)
  'bearish' — equal HIGHS were swept (price grabbed liquidity above, likely to move down)
  None      — no sweep on the last candle
"""
import pandas as pd
import numpy as np
import config


# ─── Swing Detection ─────────────────────────────────────────────────────────

def find_swing_highs(df: pd.DataFrame, window: int = None) -> list[tuple[int, float]]:
    """
    Return list of (bar_index, price) for confirmed swing highs.
    A swing high is a bar whose HIGH is the highest in the surrounding window.
    NOTE: the last `window` bars are excluded — they are not yet confirmed.
    """
    w = window or config.SWING_WINDOW
    highs = []
    # Stop before the last `w` bars so we only use confirmed swings
    for i in range(w, len(df) - w):
        window_highs = df["high"].iloc[i - w : i + w + 1]
        if df["high"].iloc[i] == window_highs.max():
            highs.append((i, df["high"].iloc[i]))
    return highs


def find_swing_lows(df: pd.DataFrame, window: int = None) -> list[tuple[int, float]]:
    """
    Return list of (bar_index, price) for confirmed swing lows.
    A swing low is a bar whose LOW is the lowest in the surrounding window.
    """
    w = window or config.SWING_WINDOW
    lows = []
    for i in range(w, len(df) - w):
        window_lows = df["low"].iloc[i - w : i + w + 1]
        if df["low"].iloc[i] == window_lows.min():
            lows.append((i, df["low"].iloc[i]))
    return lows


# ─── Equal Levels ─────────────────────────────────────────────────────────────

def find_equal_highs(
    swing_highs: list[tuple[int, float]],
    threshold: float = None,
) -> list[tuple[float, int, int]]:
    """
    Find pairs of swing highs that are within `threshold` % of each other.

    Returns list of (level, bar_a, bar_b) — most recent pairs last.
    """
    thr = threshold or config.EQUAL_LEVEL_THRESHOLD
    result = []
    for i in range(len(swing_highs) - 1):
        for j in range(i + 1, len(swing_highs)):
            price_a = swing_highs[i][1]
            price_b = swing_highs[j][1]
            if abs(price_a - price_b) / price_a < thr:
                level = (price_a + price_b) / 2  # Average of the two levels
                result.append((level, swing_highs[i][0], swing_highs[j][0]))
    return result


def find_equal_lows(
    swing_lows: list[tuple[int, float]],
    threshold: float = None,
) -> list[tuple[float, int, int]]:
    """
    Find pairs of swing lows that are within `threshold` % of each other.
    """
    thr = threshold or config.EQUAL_LEVEL_THRESHOLD
    result = []
    for i in range(len(swing_lows) - 1):
        for j in range(i + 1, len(swing_lows)):
            price_a = swing_lows[i][1]
            price_b = swing_lows[j][1]
            if abs(price_a - price_b) / price_a < thr:
                level = (price_a + price_b) / 2
                result.append((level, swing_lows[i][0], swing_lows[j][0]))
    return result


# ─── Sweep Detection ──────────────────────────────────────────────────────────

def detect_liquidity_sweep(
    df: pd.DataFrame,
    lookback: int = None,
) -> dict:
    """
    Check the most recent CLOSED candle for a liquidity sweep.

    A sweep requires:
      1. Price WICKS through an equal high/low level (the wick itself breaks the level).
      2. The candle CLOSES back on the opposite side of the level.

    Args:
        df:       15m DataFrame (must be recent candles; current bar is df.iloc[-1])
        lookback: how many candles back to look for equal levels

    Returns:
        {
            'swept':     True/False,
            'direction': 'bullish' | 'bearish' | None,
            'level':     price level that was swept (float | None),
        }
    """
    lb = lookback or config.LIQUIDITY_LOOKBACK
    analysis_df = df.iloc[-lb:]   # Only look at recent candles for speed

    # The sweep is detected on the last candle
    last = analysis_df.iloc[-1]

    # Work on all candles except the last (equal levels must be pre-formed)
    history_df = analysis_df.iloc[:-1]

    swing_highs = find_swing_highs(history_df)
    swing_lows  = find_swing_lows(history_df)

    # ── Bearish sweep: wick takes out equal HIGHS, closes below them ──────────
    equal_highs = find_equal_highs(swing_highs)
    for (level, bar_a, bar_b) in reversed(equal_highs):   # Most recent first
        wick_above  = last["high"] > level           # Wick pierced the level
        close_below = last["close"] < level          # But closed below it
        if wick_above and close_below:
            return {"swept": True, "direction": "bearish", "level": level}

    # ── Bullish sweep: wick takes out equal LOWS, closes above them ───────────
    equal_lows = find_equal_lows(swing_lows)
    for (level, bar_a, bar_b) in reversed(equal_lows):
        wick_below  = last["low"] < level            # Wick pierced the level
        close_above = last["close"] > level          # But closed above it
        if wick_below and close_above:
            return {"swept": True, "direction": "bullish", "level": level}

    return {"swept": False, "direction": None, "level": None}


def get_liquidity_details(df: pd.DataFrame) -> dict:
    """
    Extended analysis for logging / ML features.
    Returns sweep result + recent swing highs/lows.
    """
    sweep = detect_liquidity_sweep(df)
    history_df = df.iloc[-config.LIQUIDITY_LOOKBACK:-1]
    swing_highs = find_swing_highs(history_df)
    swing_lows  = find_swing_lows(history_df)

    return {
        **sweep,
        "recent_swing_highs": [p for _, p in swing_highs[-3:]],
        "recent_swing_lows":  [p for _, p in swing_lows[-3:]],
    }
