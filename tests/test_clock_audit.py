from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd
import pytest

from data.clock_audit import collect_clock_audit
from tests.test_mt5_connection_clock import Tick, adapter_at


@pytest.mark.parametrize("offset_seconds", [0, 10800])
def test_audit_keeps_raw_and_normalized_clocks_and_never_accepts_contract(monkeypatch, offset_seconds):
    now = datetime.now(timezone.utc)
    raw = now.timestamp() + offset_seconds
    adapter, fake = adapter_at(monkeypatch, now, Tick(raw, 1.1, 1.1001))
    fake.copy_rates_from_pos = lambda symbol, tf, start, count: [
        {"time": pd.Timestamp(raw, unit="s", tz="UTC").floor(f"{tf}min").timestamp()}]
    # A real acquisition boundary must not touch any brokerage submission method.
    with patch.object(fake, "order_send", side_effect=AssertionError("order API forbidden")), patch.object(fake, "order_check", side_effect=AssertionError("order API forbidden")):
        report = collect_clock_audit(adapter, "EURUSD")
    assert report["source_contract_verified"] is False
    assert report["order_apis_invoked"] is False
    assert all("rows" in result for result in report["raw_bars"].values())
    assert report["tick_samples"][0]["raw_source_minus_host_seconds"] == pytest.approx(offset_seconds, abs=2)
    assert report["connection_diagnostic"]["fresh_tick"] == (offset_seconds == 0)
    assert str(report["status"]).startswith("blocked") == (offset_seconds != 0)
    assert set(report["broker_metadata"]) == {"server", "company", "terminal_company"}
    assert "balance" not in report["connection_diagnostic"]
