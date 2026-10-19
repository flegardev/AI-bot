"""Causal descriptive buckets, never entry-threshold optimization."""
import numpy as np
import pandas as pd


def describe_market(frame, timestamp, spread):
    close = frame["close"]
    atr = float(pd.concat([frame["high"] - frame["low"],
        (frame["high"] - close.shift()).abs(), (frame["low"] - close.shift()).abs()], axis=1).max(axis=1).tail(14).mean())
    price = float(close.iloc[-1])
    atr_pct = atr / price if price else 0.0
    ema = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
    hour = pd.Timestamp(timestamp).hour
    volatility = float(close.pct_change().tail(20).std())
    return {"utc_session": "asia" if hour < 7 else "europe" if hour < 13 else "us" if hour < 21 else "rollover",
            "volatility_bucket": "low" if atr_pct < .001 else "high" if atr_pct > .01 else "medium",
            "market_regime": "trend" if atr > 0 and abs(price - ema) / atr > 1 else "range",
            "spread_bucket": "low" if spread / max(atr, 1e-12) < .1 else "high",
            "volatility_20": volatility if np.isfinite(volatility) else 0.0,
            "entry_month": pd.Timestamp(timestamp).strftime("%Y-%m"), "observed_spread": float(spread)}
