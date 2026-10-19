"""Synthetic broker-clock contracts; no real orders or inferred timezone offsets."""
from collections import namedtuple
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import brokers.mt5 as mt5_module
import config
from brokers.mt5 import MT5BrokerAdapter
from tests.test_mt5_adapter import FakeMT5

Tick = namedtuple("Tick", "time bid ask")


def adapter_at(monkeypatch, now, tick, source_timezone="UTC"):
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz else now.replace(tzinfo=None)
    monkeypatch.setattr(mt5_module, "datetime", FixedClock)
    monkeypatch.setenv("MT5_SOURCE_TIMEZONE", source_timezone)
    fake = FakeMT5()
    fake.symbol_info_tick = lambda symbol: tick
    return MT5BrokerAdapter(fake), fake


def test_connection_diagnostic_rejects_three_hour_future_quote(monkeypatch):
    now = datetime(2024, 7, 1, 9, tzinfo=timezone.utc)
    adapter, fake = adapter_at(monkeypatch, now, Tick((now + timedelta(hours=3)).timestamp(), 1.1, 1.1001))
    result = adapter.check_connection("EURUSD")
    assert result["fresh_tick"] is False
    assert result["ok"] is False
    assert result["price_age_seconds"] == pytest.approx(-10800)
    assert fake.sent == []


@pytest.mark.parametrize("age", [-10800, -5.001, -5, 0, 120, 120.001])
def test_diagnostic_matches_shared_quote_timestamp_policy(monkeypatch, age):
    from dataclasses import replace
    monkeypatch.setattr(config, "SETTINGS", replace(config.SETTINGS, stale_price_seconds=120))
    now = datetime(2024, 7, 1, 9, tzinfo=timezone.utc)
    adapter, fake = adapter_at(monkeypatch, now, Tick((now - timedelta(seconds=age)).timestamp(), 1.1, 1.1001))
    result = adapter.check_connection("EURUSD")
    quote = adapter.get_quote("EURUSD")
    try:
        quote.validate(120, config.SETTINGS.max_spread, now)
        accepted = True
    except ValueError:
        accepted = False
    assert result["fresh_tick"] == accepted
    assert result["ok"] == accepted
    assert result["price_age_seconds"] == pytest.approx(age, abs=1e-5)
    assert fake.sent == []


@pytest.mark.parametrize("bid,ask", [(1.1, 1.0999), (0, .0001), (float("nan"), 1.1), (1.1, float("inf")), (1.1, 1.2)])
def test_diagnostic_rejects_bad_quote_prices_even_when_timestamp_is_fresh(monkeypatch, bid, ask):
    now = datetime(2024, 7, 1, 9, tzinfo=timezone.utc)
    adapter, fake = adapter_at(monkeypatch, now, Tick(now.timestamp(), bid, ask))
    result = adapter.check_connection("EURUSD")
    assert result["valid_spread"] is False
    assert result["ok"] is False
    assert fake.sent == []


@pytest.mark.parametrize("wall,utc", [("2024-01-15 12:00", "2024-01-15 10:00Z"), ("2024-07-15 12:00", "2024-07-15 09:00Z"),
                                      ("2024-03-31 02:55", "2024-03-31 00:55Z"), ("2024-03-31 04:05", "2024-03-31 01:05Z")])
def test_declared_dst_contract_applies_to_quote_diagnostic_and_history_requests(monkeypatch, wall, utc):
    expected = pd.Timestamp(utc)
    raw = pd.Timestamp(wall).tz_localize("UTC").timestamp()
    adapter, fake = adapter_at(monkeypatch, expected.to_pydatetime(), Tick(raw, 1.1, 1.1001), "Europe/Helsinki")
    result = adapter.check_connection("EURUSD")
    assert result["ok"] and result["fresh_tick"]
    assert result["price_age_seconds"] == pytest.approx(0)
    assert adapter.get_quote("EURUSD").timestamp == expected.to_pydatetime()
    assert adapter._request_time(expected).timestamp() == raw
    assert fake.sent == []


@pytest.mark.parametrize("wall", ["2024-03-31 03:30", "2024-10-27 03:30"])
def test_ambiguous_or_nonexistent_dst_wall_time_is_not_accepted(monkeypatch, wall):
    now = datetime(2024, 7, 1, 9, tzinfo=timezone.utc)
    raw = pd.Timestamp(wall).tz_localize("UTC").timestamp()
    adapter, fake = adapter_at(monkeypatch, now, Tick(raw, 1.1, 1.1001), "Europe/Helsinki")
    result = adapter.check_connection("EURUSD")
    assert not result["fresh_tick"] and not result["ok"]
    assert result["quote_errors"]
    assert fake.sent == []


@pytest.mark.parametrize("tick", [None, Tick(None, 1.1, 1.1001), Tick(0, 1.1, 1.1001), Tick(float("nan"), 1.1, 1.1001)])
def test_missing_or_invalid_timestamp_fails_closed(monkeypatch, tick):
    now = datetime(2024, 7, 1, 9, tzinfo=timezone.utc)
    adapter, fake = adapter_at(monkeypatch, now, tick)
    result = adapter.check_connection("EURUSD")
    assert not result["fresh_tick"] and not result["ok"]
    assert result["quote_errors"]
    assert fake.sent == []
