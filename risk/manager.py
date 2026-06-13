"""
risk/manager.py — Position sizing, stop-loss, and take-profit calculation.

Rules enforced:
  - Risk per trade = 1% of account balance
  - Minimum R:R    = 1:2  (take-profit at least 2× the stop distance)
  - Stop-loss placed below (long) or above (short) the swept liquidity level
    or the broken structure level, with a small ATR buffer
"""
import config
import logger


def calculate_position(
    direction:       str,    # 'LONG' or 'SHORT'
    entry_price:     float,
    stop_loss:       float,
    account_balance: float,
    risk_pct:        float = None,
    min_rr:          float = None,
) -> dict | None:
    """
    Calculate trade parameters from entry, SL, and account size.

    Args:
        direction:       'LONG' or 'SHORT'
        entry_price:     planned entry price
        stop_loss:       stop-loss price
        account_balance: current account balance in quote currency (e.g. USDT)
        risk_pct:        fraction of balance to risk (default from config)
        min_rr:          minimum R:R ratio (default from config)

    Returns:
        dict with keys: entry, stop_loss, take_profit, size, risk_amount, rr_ratio
        None if the trade does not meet minimum R:R requirements.
    """
    risk_pct = risk_pct or config.RISK_PER_TRADE
    min_rr   = min_rr   or config.MIN_RISK_REWARD

    risk_amount = account_balance * risk_pct     # e.g. $100 on a $10,000 account
    sl_distance = abs(entry_price - stop_loss)

    if direction == "LONG" and stop_loss >= entry_price:
        logger.warning("LONG stop-loss is not below entry - skipping trade.")
        return None
    if direction == "SHORT" and stop_loss <= entry_price:
        logger.warning("SHORT stop-loss is not above entry - skipping trade.")
        return None

    if sl_distance == 0:
        logger.warning("SL distance is zero — skipping trade.")
        return None

    # Position size in base currency (e.g. BTC quantity)
    size = risk_amount / sl_distance

    # Take-profit: minimum 2× the risk distance in the trade's favour
    tp_distance = sl_distance * min_rr
    if direction == "LONG":
        take_profit = entry_price + tp_distance
        rr_actual   = (take_profit - entry_price) / sl_distance
    else:
        take_profit = entry_price - tp_distance
        rr_actual   = (entry_price - take_profit) / sl_distance

    if rr_actual < min_rr:
        logger.warning(f"R:R {rr_actual:.2f} below minimum {min_rr} — skipping trade.")
        return None

    return {
        "direction":   direction,
        "entry":       entry_price,
        "stop_loss":   round(stop_loss,   6),
        "take_profit": round(take_profit, 6),
        "size":        round(size,        6),
        "risk_amount": round(risk_amount, 4),
        "rr_ratio":    round(rr_actual,   2),
    }


def calculate_stop_loss(
    direction:     str,
    liquidity_level: float,
    atr:           float,
    buffer_mult:   float = 0.5,  # Extra ATR buffer beyond the level
) -> float:
    """
    Place SL just beyond the swept liquidity level with an ATR buffer.

    For a LONG trade the SL sits BELOW the swept low level.
    For a SHORT trade the SL sits ABOVE the swept high level.

    Args:
        direction:        'LONG' or 'SHORT'
        liquidity_level:  price level of the sweep (equal high/low)
        atr:              Average True Range (used as buffer)
        buffer_mult:      multiplier on ATR for the extra buffer

    Returns:
        stop_loss price
    """
    buffer = atr * buffer_mult
    if direction == "LONG":
        return liquidity_level - buffer
    else:
        return liquidity_level + buffer


def build_trade(
    direction:       str,
    entry_price:     float,
    liquidity_level: float,
    atr:             float,
    account_balance: float,
) -> dict | None:
    """
    Full trade setup: calculates SL, TP, and size in one call.

    Args:
        direction:        'LONG' or 'SHORT'
        entry_price:      intended entry price
        liquidity_level:  price level that was swept (used for SL placement)
        atr:              ATR from the 5m chart for buffer sizing
        account_balance:  account equity in quote currency

    Returns:
        Complete trade dict or None if trade doesn't qualify.
    """
    stop_loss = calculate_stop_loss(direction, liquidity_level, atr)
    return calculate_position(direction, entry_price, stop_loss, account_balance)
