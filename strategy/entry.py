"""
strategy/entry.py - 5m entry confirmation via CHoCH and BOS.
"""
import pandas as pd

import config
from strategy.liquidity import find_swing_highs, find_swing_lows


def _classify_structure(swing_highs: list, swing_lows: list) -> str:
    """Determine recent market structure from the last two swing highs/lows."""
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "neutral"

    hh = swing_highs[-1][1] > swing_highs[-2][1]
    hl = swing_lows[-1][1] > swing_lows[-2][1]
    lh = swing_highs[-1][1] < swing_highs[-2][1]
    ll = swing_lows[-1][1] < swing_lows[-2][1]

    if hh and hl:
        return "bullish"
    if lh and ll:
        return "bearish"
    return "neutral"


def detect_entry_signal(
    df_5m: pd.DataFrame,
    sweep_direction: str,
    lookback: int = None,
) -> dict:
    """Look for a CHoCH or BOS on the 5m chart that confirms the sweep."""
    lb = lookback or config.STRUCTURE_LOOKBACK
    df = df_5m.iloc[-lb:]

    history = df.iloc[:-1]
    current = df.iloc[-1]

    swing_highs = find_swing_highs(history, window=3)
    swing_lows = find_swing_lows(history, window=3)

    if not swing_highs or not swing_lows:
        return {"signal": False, "type": None, "direction": None, "broken_level": None}

    prior_structure = _classify_structure(swing_highs, swing_lows)
    last_swing_high = swing_highs[-1][1]
    last_swing_low = swing_lows[-1][1]

    if sweep_direction == "bullish":
        if prior_structure == "bearish":
            for idx in range(len(swing_highs) - 1, -1, -1):
                prev_high = swing_highs[idx - 1][1] if idx > 0 else float("inf")
                if swing_highs[idx][1] < prev_high:
                    choch_level = swing_highs[idx][1]
                    if current["close"] > choch_level:
                        return {
                            "signal": True,
                            "type": "CHoCH",
                            "direction": "bullish",
                            "broken_level": choch_level,
                        }
                    break

        if current["close"] > last_swing_high:
            return {
                "signal": True,
                "type": "BOS",
                "direction": "bullish",
                "broken_level": last_swing_high,
            }

    elif sweep_direction == "bearish":
        if prior_structure == "bullish":
            for idx in range(len(swing_lows) - 1, -1, -1):
                prev_low = swing_lows[idx - 1][1] if idx > 0 else float("-inf")
                if swing_lows[idx][1] > prev_low:
                    choch_level = swing_lows[idx][1]
                    if current["close"] < choch_level:
                        return {
                            "signal": True,
                            "type": "CHoCH",
                            "direction": "bearish",
                            "broken_level": choch_level,
                        }
                    break

        if current["close"] < last_swing_low:
            return {
                "signal": True,
                "type": "BOS",
                "direction": "bearish",
                "broken_level": last_swing_low,
            }

    return {"signal": False, "type": None, "direction": None, "broken_level": None}


def get_entry_details(df_5m: pd.DataFrame, sweep_direction: str) -> dict:
    """Return entry signal details plus RSI and ATR features."""
    signal = detect_entry_signal(df_5m, sweep_direction)

    close = df_5m["close"]
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = (100 - (100 / (1 + rs))).fillna(50.0).iloc[-1]

    hl = df_5m["high"] - df_5m["low"]
    hpc = (df_5m["high"] - df_5m["close"].shift()).abs()
    lpc = (df_5m["low"] - df_5m["close"].shift()).abs()
    tr = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().fillna(0.0).iloc[-1]

    current_price = df_5m["close"].iloc[-1]

    return {
        **signal,
        "rsi_14": rsi,
        "atr_14": atr,
        "atr_pct": (atr / current_price) if current_price else 0.0,
        "current_price": current_price,
    }
