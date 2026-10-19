"""Conditional FX direction attribution at identical recorded times and volume."""
def exposure_matched(trades, info, slippage_pct):
    totals = {"always_long": 0.0, "always_short": 0.0, "flat": 0.0}
    matched = 0
    for trade in trades:
        if any(trade.get(key) is None for key in ("entry_midpoint", "exit_midpoint", "entry_spread", "exit_spread")):
            continue
        entry, exit_ = trade["entry_midpoint"], trade["exit_midpoint"]
        factor = trade["volume"] * info.tick_value / info.tick_size
        costs = ((trade["entry_spread"] + trade["exit_spread"]) / 2 + (abs(entry) + abs(exit_)) * slippage_pct) * factor
        costs += trade["fees"] + trade["swap"]
        totals["always_long"] += (exit_ - entry) * factor - costs
        totals["always_short"] += (entry - exit_) * factor - costs
        matched += 1
    return {"net_pnl": totals, "matched_intervals": matched, "unavailable_intervals": len(trades) - matched,
        "basis": "same strategy-conditioned times, volume and midpoint path; market liquidation on both sides",
        "limitation": "ex-post exposure attribution only; timing comes from the evaluated strategy, not an independent tradable selection signal"}
