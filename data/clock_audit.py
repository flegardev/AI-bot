"""Read-only raw/normalized MT5 clock evidence, without inferring broker offsets."""
from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any

import pandas as pd

from core.provenance import git_revision, runtime_identity, source_hash
from core.timeframes import timeframe_to_timedelta

API_TIME_REFERENCE = "https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesrange_py"


def collect_clock_audit(adapter, symbol):
    """Caller owns disconnect. Only terminal/account metadata and market reads are used."""
    diagnostic = adapter.check_connection(symbol)
    terminal = adapter.mt5.terminal_info()
    account = adapter.mt5.account_info()
    report: dict[str, Any] = {"schema": "mt5-clock-audit-v1", "symbol": symbol, "mode": "read_only",
        "git_sha": git_revision(), "source_sha256": source_hash(), "runtime": runtime_identity(),
        "observed_at": datetime.now(timezone.utc).isoformat(), "source_timezone_configured": adapter.source_timezone,
        "api_documented_convention": "UTC without an offset", "api_reference": API_TIME_REFERENCE,
        "broker_metadata": {"server": getattr(account, "server", None), "company": getattr(account, "company", None),
                            "terminal_company": getattr(terminal, "company", None)},
        "connection_diagnostic": {key: diagnostic.get(key) for key in ("ok", "fresh_tick", "valid_spread",
            "price_age_seconds", "source_timestamp_utc", "observed_at", "quote_errors")},
        "order_apis_invoked": False, "source_contract_verified": False,
        "limitations": ["A clock offset observation does not identify a timezone or establish its DST rules.",
            "Historical DST behavior requires broker documentation and independent historical references.",
            "This audit does not qualify history coverage, commissions, rollover or trading performance."]}
    samples: list[dict[str, Any]] = []
    for _ in range(3):
        before = datetime.now(timezone.utc)
        started = perf_counter()
        tick = adapter.mt5.symbol_info_tick(symbol)
        duration = perf_counter() - started
        received = datetime.now(timezone.utc)
        sample: dict[str, Any] = {"host_before_utc": before.isoformat(), "host_received_utc": received.isoformat()}
        try:
            quote = adapter._quote_from_tick(tick)
            raw = pd.Timestamp(float(tick.time), unit="s", tz="UTC")
            sample.update(raw_tick_epoch_seconds=float(tick.time), raw_tick_epoch_milliseconds=getattr(tick, "time_msc", None),
                raw_tick_as_utc=raw.isoformat(), normalized_tick_utc=quote.timestamp.isoformat(),
                signed_age_seconds=quote.age_seconds(received),
                raw_source_minus_host_seconds=(raw.to_pydatetime(warn=False) - received).total_seconds(),
                spread=quote.spread, read_call_seconds=duration)
        except Exception as exc:
            sample["error"] = str(exc)
        samples.append(sample)
    report["tick_samples"] = samples
    bars: dict[str, Any] = {}
    for tf, constant in (("1h", "TIMEFRAME_H1"), ("15m", "TIMEFRAME_M15"), ("5m", "TIMEFRAME_M5")):
        received = datetime.now(timezone.utc)
        try:
            rates = adapter.mt5.copy_rates_from_pos(symbol, getattr(adapter.mt5, constant), 0, 8)
            if rates is None:
                raise ValueError(f"MT5 history read failed: {adapter.mt5.last_error()}")
            frame = pd.DataFrame(rates)
            if frame.empty:
                raise ValueError("no raw bars available")
            opens = [adapter._source_timestamp(float(value)) for value in frame["time"]]
            rows = [{"raw_epoch_seconds": float(value), "raw_open_as_utc": pd.Timestamp(float(value), unit="s", tz="UTC").isoformat(),
                "normalized_open_utc": opened.isoformat(), "normalized_close_utc": (opened + timeframe_to_timedelta(tf)).isoformat(),
                "completed_at_observation": bool(opened + timeframe_to_timedelta(tf) <= received),
                "open_beyond_future_tolerance": bool(opened > pd.Timestamp(received) + pd.Timedelta(seconds=5))}
                for value, opened in zip(frame["time"], opens, strict=True)]
            bars[tf] = {"host_utc": received.isoformat(), "rows": rows,
                "raw_tick_minus_latest_bar_seconds": samples[-1].get("raw_tick_epoch_seconds", 0) - float(frame["time"].iloc[-1])}
        except Exception as exc:
            bars[tf] = {"error": str(exc)}
    report["raw_bars"] = bars
    failed = not diagnostic["ok"] or any("error" in sample for sample in samples)
    failed |= any("error" in result or any(row["open_beyond_future_tolerance"] for row in result.get("rows", [])) for result in bars.values())
    report["status"] = "blocked_clock_or_source" if failed else "observations_only_contract_not_verified"
    return report
